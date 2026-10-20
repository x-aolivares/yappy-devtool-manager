"""Login a AWS SSO por device flow.

Acá y no en el CLI por una razón concreta: `Session.start()` —que vive en la
library— abre un login cuando no hay sesión viva, y la library no puede
importar el CLI. La versión anterior lo intentaba igual, con un import relativo
que apuntaba a `yappy_library.aws.session`: un paquete que no existe. El
resultado era que `Session.start()` —el camino que se toma justamente cuando
falta la sesión— fallaba con `ModuleNotFoundError` en vez de abrir el
navegador.

Dos cosas viajan juntas y por eso viven juntas: el `sso-oidc` client de botocore
y el cache del token en `~/.aws/sso/cache`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import webbrowser
from pathlib import Path

import botocore.session
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .aws_ini import aws_dir, read_ini, write_atomic
from .logging import die, info, success

#: Cuánto vive un device code de SSO si AWS no dice otra cosa.
#:
#: Es el deadline del polling: sin él, un login que el developer nunca
#: completa en el navegador deja el comando esperando para siempre.
DEFAULT_DEVICE_CODE_SECONDS = 600


def sso_cache_path(session_name: str) -> Path:
    """La carpeta del cache de SSO para una sesión, con el nombre que usa AWS CLI."""
    key = hashlib.sha1(session_name.encode("utf-8")).hexdigest()
    path = aws_dir() / "sso" / "cache" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _sso_oidc_client(region: str):
    """A botocore SSO-OIDC client that ignores the environment's AWS profile.

    The device flow does not sign requests, but botocore still resolves the
    profile when it builds the client. With a profile that carries a
    ``credential_process`` that means running an external command to get a
    credential this client will never use — and with a profile that does not
    exist it is a ``ProfileNotFound`` and no login at all.

    The old implementation popped ``AWS_PROFILE``, ``AWS_DEFAULT_PROFILE`` and
    ``AWS_SHARED_CREDENTIALS_FILE`` from ``os.environ`` and put them back in a
    ``finally``. That is process-global state: between the pop and the
    restore, every other thread sees a different AWS profile — and the server
    is multi-threaded. The override below belongs to one session, so only this
    client sees it.
    """
    session = botocore.session.Session()
    # `Session.set_config_variable` no sirve acá: escribe en los
    # `_session_instance_vars`, que no alimentan a
    # `get_config_variable('profile')` — el profile se resuelve de otro lado
    # y el override ahí no se ve.
    store = session.get_component("config_store")
    store.set_config_variable("profile", None)
    store.set_config_variable("credentials_file", str(aws_dir() / "credentials"))
    return session.create_client(
        "sso-oidc",
        region_name=region,
        config=BotoConfig(signature_version=UNSIGNED),
    )


def _sso_settings(profile: str) -> tuple[str, str, str]:
    """Del `~/.aws/config` del profile, el `start_url`, la región y el nombre de sesión.

    `env_a` es destino y `env_b` es origen en toda la app; acá no hay dos
    lados, hay un profile.
    """
    config_path = aws_dir() / "config"
    if not config_path.exists():
        die("~/.aws/config not found")
    _order, config = read_ini(config_path)

    profile_key = f"profile {profile}"
    if profile_key not in config:
        die(f"Profile '{profile}' not found in ~/.aws/config")

    prof = config[profile_key]
    sso_session_name = prof.get("sso_session")
    if not sso_session_name:
        die(f"No sso_session configured for profile '{profile}'")

    session_key = f"sso-session {sso_session_name}"
    if session_key not in config:
        die(f"SSO session '{sso_session_name}' not found in ~/.aws/config")

    sso_cfg = config[session_key]
    return (
        sso_cfg.get("sso_start_url", ""),
        sso_cfg.get("sso_region", "us-east-1"),
        sso_session_name,
    )


def login(profile: str) -> Path:
    """Abrir el navegador, esperar la autorización y cachear el token.

    Devuelve la ruta del cache para que el caller pueda informarla. Falla con
    `die` —que es `sys.exit`— si el profile no tiene SSO configurado.
    """
    start_url, sso_region, sso_session_name = _sso_settings(profile)

    info(f"SSO start URL: {start_url}")
    info(f"SSO region: {sso_region}")

    client = _sso_oidc_client(sso_region)

    # 1 — Register OAuth client
    info("Registering OAuth client with AWS SSO...")
    reg = client.register_client(clientName="yappy-cli", clientType="public")
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]

    # 2 — Device authorization
    auth = client.start_device_authorization(
        clientId=client_id,
        clientSecret=client_secret,
        startUrl=start_url,
    )

    # 3 — Open browser
    info("Opening browser for SSO login...")
    webbrowser.open(auth["verificationUriComplete"])
    info(f"Your device code: [bold]{auth['userCode']}[/bold]")
    info("If the browser doesn't open, visit:")
    info(f"  {auth['verificationUriComplete']}")

    # 4 — Poll for token
    interval = auth.get("interval", 5)
    # El device code tiene vencimiento. Sin deadline, un developer que nunca
    # completa el login en el navegador deja el comando esperando para
    # siempre: el polling no termina nunca y el proceso no vuelve.
    expires_in = auth.get("expiresIn", DEFAULT_DEVICE_CODE_SECONDS)
    deadline = time.monotonic() + expires_in
    info(f"Waiting for authorization (polling every {interval}s)...")
    while True:
        try:
            token = client.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=auth["deviceCode"],
            )
            break
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "AuthorizationPendingException":
                if time.monotonic() >= deadline:
                    die(
                        f"El login de SSO expiró después de {expires_in}s. "
                        "Volvé a correr el comando y completá la autorización "
                        "en el navegador."
                    )
                time.sleep(interval)
            else:
                die(f"SSO authorization failed: {e}")

    # 5 — Cache the token (AWS CLI v2 compatible format)
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + token["expiresIn"]),
    )
    cache = {
        "startUrl": start_url,
        "region": sso_region,
        "accessToken": token["accessToken"],
        "expiresAt": expires_at,
        "clientId": client_id,
        "clientSecret": client_secret,
        "registrationExpiresAt": expires_at,
    }
    cache_path = sso_cache_path(sso_session_name)
    write_atomic(cache_path, json.dumps(cache, indent=2))

    success(f"SSO session established (expires: {expires_at})")
    success(f"Token cached: {cache_path}")
    return cache_path
