"""El login SSO: sin estado global y con deadline.

Dos cosas que estaban rotas y que sólo se ven en el camino que hoy nadie
recorre —el developer sin sesión, en una máquina con `AWS_PROFILE` seteado—.

1. **`os.environ` global.** El client de `sso-oidc` se creaba sacando
   `AWS_PROFILE`, `AWS_DEFAULT_PROFILE` y `AWS_SHARED_CREDENTIALS_FILE` del
   entorno y poniéndolos después. Eso es estado global del proceso: entre el
   pop y el restore, cualquier otro hilo veía otro profile de AWS — y el
   server es multi-hilo—.

2. **El polling no tenía deadline.** Si el developer nunca completaba la
   autorización en el navegador, el comando no volvía nunca.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from yappy_library.adapters import aws_sso


def _pending() -> ClientError:
    return ClientError(
        {"Error": {"Code": "AuthorizationPendingException", "Message": "waiting"}},
        "CreateToken",
    )


# --- el client no toca el entorno ------------------------------------------


def test_el_client_de_sso_no_toca_el_entorno(monkeypatch):
    """El entorno es del proceso, no de este client.

    Lo que importa no es el estado en el que queda el entorno *después* de la
    llamada —el pop lo devolvía— sino el estado *durante* la creación del
    client: esa es la ventana en la que otro hilo veía otro profile de AWS.
    """
    import botocore.session
    import os

    monkeypatch.setenv("AWS_PROFILE", "base-profile")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/mi/credentials")

    observado = {}
    original = botocore.session.Session.create_client

    def _spy(self, *args, **kwargs):
        observado["profile"] = os.environ.get("AWS_PROFILE")
        observado["credentials_file"] = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(botocore.session.Session, "create_client", _spy)

    aws_sso._sso_oidc_client("us-east-1")

    assert observado["profile"] == "base-profile", (
        "el entorno estaba modificado mientras se creaba el client"
    )
    assert observado["credentials_file"] == "/mi/credentials"


def test_el_client_de_sso_deja_el_entorno_como_estaba(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "base-profile")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/mi/credentials")

    aws_sso._sso_oidc_client("us-east-1")

    import os

    assert os.environ["AWS_PROFILE"] == "base-profile"
    assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == "/mi/credentials"


def test_un_profile_que_no_existe_no_tira(monkeypatch):
    """Sin el override, un `AWS_PROFILE` que no existe es un ProfileNotFound.

    Con el pop pasaba; con el override mal puesto, no. El pop era la razón de
    que funcionara, y la razón de que fuera peligroso.
    """
    monkeypatch.setenv("AWS_PROFILE", "un-profile-que-no-existe")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "otro-que-no-existe")

    client = aws_sso._sso_oidc_client("us-east-1")

    assert client is not None


def test_el_client_se_puede_crear_en_hilos_paralelos(monkeypatch):
    """Dos threads creando el client a la vez no se pisan.

    Con el pop, el segundo thread podía ver el entorno sin `AWS_PROFILE`
    mientras el primero todavía no lo había devuelto.
    """
    import threading

    monkeypatch.setenv("AWS_PROFILE", "base-profile")
    resultados = []
    errores = []

    def _crear():
        try:
            resultados.append(aws_sso._sso_oidc_client("us-east-1"))
        except Exception as exc:  # pragma: no cover - sólo para el assert
            errores.append(exc)

    hilos = [threading.Thread(target=_crear) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, errores
    assert len(resultados) == 4

# --- el polling tiene deadline ---------------------------------------------


def _fake_client(monkeypatch, *, pending_forever: bool, expires_in: int = 600):
    """Un client que siempre dice "todavía no"."""
    llamadas = {"n": 0}

    class _Client:
        def register_client(self, **kwargs):
            return {"clientId": "id", "clientSecret": "secret"}

        def start_device_authorization(self, **kwargs):
            return {
                "deviceCode": "code",
                "userCode": "ABCD",
                "verificationUriComplete": "https://example.test/device",
                "interval": 1,
                "expiresIn": expires_in,
            }

        def create_token(self, **kwargs):
            llamadas["n"] += 1
            if pending_forever:
                raise _pending()
            return {"accessToken": "tok", "expiresIn": 3600}

    monkeypatch.setattr(aws_sso, "_sso_oidc_client", lambda region: _Client())
    monkeypatch.setattr(aws_sso.webbrowser, "open", lambda url: None)
    return llamadas


def _fake_aws_dir(monkeypatch, tmp_path):
    """Un `~/.aws` de mentira, con el `config` que el login espera encontrar."""
    aws = tmp_path / ".aws"
    aws.mkdir(parents=True, exist_ok=True)
    (aws / "config").write_text("[profile base]\nsso_session = mi-sesion\n", encoding="utf-8")
    monkeypatch.setattr(aws_sso, "aws_dir", lambda: aws)
    monkeypatch.setattr(aws_sso, "read_ini", lambda path: ([], {
        "profile base": {"sso_session": "mi-sesion"},
        "sso-session mi-sesion": {"sso_start_url": "https://example.test", "sso_region": "us-east-1"},
    }))
    return aws


def test_el_login_termina_cuando_llega_el_token(monkeypatch, tmp_path):
    _fake_client(monkeypatch, pending_forever=False)
    _fake_aws_dir(monkeypatch, tmp_path)

    cache = aws_sso.login("base")

    assert cache.exists()
    assert "accessToken" in cache.read_text(encoding="utf-8")


def test_el_login_abandona_cuando_el_device_code_expira(monkeypatch, tmp_path):
    """Sin deadline, el comando no volvía nunca."""
    _fake_client(monkeypatch, pending_forever=True, expires_in=0)
    _fake_aws_dir(monkeypatch, tmp_path)
    # El sleep no espera de verdad: el test no tiene por qué tardar un segundo.
    monkeypatch.setattr(aws_sso.time, "sleep", lambda seconds: None)

    with pytest.raises(SystemExit) as exc_info:
        aws_sso.login("base")

    assert exc_info.value.code == 1


def test_el_default_del_device_code_es_un_deadline(monkeypatch):
    """Si AWS no dice cuánto vive el device code, el deadline es el del código."""
    assert aws_sso.DEFAULT_DEVICE_CODE_SECONDS > 0
    assert isinstance(aws_sso.DEFAULT_DEVICE_CODE_SECONDS, int)
