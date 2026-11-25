"""Estado y login de sesión AWS/SSO para la web."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException

from yappy_library.adapters.aws_ini import aws_dir, read_ini
from yappy_library.adapters.aws_sso import (
    DEFAULT_DEVICE_CODE_SECONDS,
    _sso_oidc_client,
    sso_cache_path,
)
from yappy_library.adapters.file_io import write_atomic
from yappy_library.config import Config

from ..aws_auth import cache_status
from ..schemas import AwsSessionLoginPollResponse, AwsSessionLoginStartResponse, AwsSessionStatusResponse

router = APIRouter(tags=["aws"])


@dataclass
class _LoginState:
    login_id: str
    profile: str
    sso_session: str
    start_url: str
    sso_region: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    created_at: float = field(default_factory=time.monotonic)
    status: str = "pending"  # pending | authorized | expired | error
    message: str | None = None
    expires_at: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_LOGINS: dict[str, _LoginState] = {}
_LOGINS_LOCK = threading.Lock()


def _default_profile() -> str:
    try:
        return Config().profile
    except Exception:
        return "base-profile"


def _sso_settings_safe(profile: str) -> tuple[str, str, str]:
    config_path = aws_dir() / "config"
    if not config_path.exists():
        raise HTTPException(status_code=400, detail="No se encontró ~/.aws/config.")
    _order, config = read_ini(config_path)
    profile_key = f"profile {profile}"
    if profile_key not in config:
        raise HTTPException(status_code=400, detail=f"No se encontró el perfil '{profile}' en ~/.aws/config.")
    prof = config[profile_key]
    sso_session_name = prof.get("sso_session")
    if not sso_session_name:
        raise HTTPException(status_code=400, detail=f"El perfil '{profile}' no tiene sso_session configurado.")
    session_key = f"sso-session {sso_session_name}"
    if session_key not in config:
        raise HTTPException(status_code=400, detail=f"No se encontró la sesión SSO '{sso_session_name}' en ~/.aws/config.")
    sso_cfg = config[session_key]
    return (
        sso_cfg.get("sso_start_url", ""),
        sso_cfg.get("sso_region", "us-east-1"),
        sso_session_name,
    )


def _status_for_profile(profile: str) -> dict:
    try:
        start_url, _sso_region, sso_session_name = _sso_settings_safe(profile)
    except HTTPException as exc:
        return {
            "active": False,
            "profile": profile,
            "sso_session": None,
            "start_url": None,
            "expires_at": None,
            "expires_in_seconds": None,
            "message": str(exc.detail),
        }
    except Exception as exc:
        return {
            "active": False,
            "profile": profile,
            "sso_session": None,
            "start_url": None,
            "expires_at": None,
            "expires_in_seconds": None,
            "message": str(exc),
        }
    return cache_status(sso_cache_path(sso_session_name), profile=profile, sso_session=sso_session_name, start_url=start_url)


def _poll_login(state: _LoginState, client_id: str, client_secret: str, device_code: str, interval: int, deadline: float) -> None:
    import json

    client = _sso_oidc_client(state.sso_region)
    while True:
        with state.lock:
            if state.status != "pending":
                return
        try:
            token = client.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=device_code,
            )
            expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + token["expiresIn"]))
            cache = {
                "startUrl": state.start_url,
                "region": state.sso_region,
                "accessToken": token["accessToken"],
                "expiresAt": expires_at,
                "clientId": client_id,
                "clientSecret": client_secret,
                "registrationExpiresAt": expires_at,
            }
            write_atomic(sso_cache_path(state.sso_session), json.dumps(cache, indent=2))
            with state.lock:
                state.status = "authorized"
                state.expires_at = expires_at
                state.message = "Sesión de AWS autenticada correctamente."
            return
        except Exception as exc:  # noqa: BLE001
            code = ""
            resp = getattr(exc, "response", None)
            if isinstance(resp, dict):
                code = resp.get("Error", {}).get("Code", "")
            if code == "AuthorizationPendingException":
                if time.monotonic() >= deadline:
                    with state.lock:
                        state.status = "expired"
                        state.message = "El login de SSO expiró. Volvé a intentarlo."
                    return
                time.sleep(max(1, interval))
                continue
            if code == "SlowDownException":
                interval += 5
                time.sleep(interval)
                continue
            if code == "ExpiredTokenException":
                with state.lock:
                    state.status = "expired"
                    state.message = "El login de SSO expiró. Volvé a intentarlo."
                return
            with state.lock:
                state.status = "error"
                state.message = f"Error al autenticar con AWS SSO: {exc}"
            return


@router.get("/api/aws/session/status", operation_id="aws_session_status", response_model=AwsSessionStatusResponse)
def aws_session_status():
    profile = _default_profile()
    return _status_for_profile(profile)


@router.post("/api/aws/session/login/start", operation_id="aws_session_login_start", response_model=AwsSessionLoginStartResponse)
def aws_session_login_start():
    profile = _default_profile()
    start_url, sso_region, sso_session_name = _sso_settings_safe(profile)
    client = _sso_oidc_client(sso_region)
    try:
        reg = client.register_client(clientName="yappy-cli", clientType="public")
        auth = client.start_device_authorization(
            clientId=reg["clientId"],
            clientSecret=reg["clientSecret"],
            startUrl=start_url,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo iniciar el login de AWS SSO: {exc}") from exc

    login_id = uuid.uuid4().hex
    expires_in = int(auth.get("expiresIn", DEFAULT_DEVICE_CODE_SECONDS))
    interval = int(auth.get("interval", 5))
    state = _LoginState(
        login_id=login_id,
        profile=profile,
        sso_session=sso_session_name,
        start_url=start_url,
        sso_region=sso_region,
        user_code=auth.get("userCode", ""),
        verification_uri=auth.get("verificationUri", ""),
        verification_uri_complete=auth.get("verificationUriComplete", ""),
        expires_in=expires_in,
    )
    with _LOGINS_LOCK:
        _LOGINS[login_id] = state

    deadline = time.monotonic() + expires_in
    thread = threading.Thread(
        target=_poll_login,
        args=(state, reg["clientId"], reg["clientSecret"], auth["deviceCode"], interval, deadline),
        daemon=True,
        name=f"aws-sso-login-{login_id[:8]}",
    )
    thread.start()

    return {
        "login_id": login_id,
        "verification_uri": state.verification_uri,
        "verification_uri_complete": state.verification_uri_complete,
        "user_code": state.user_code,
        "expires_in": expires_in,
        "profile": profile,
    }


@router.get("/api/aws/session/login/{login_id}", operation_id="aws_session_login_poll", response_model=AwsSessionLoginPollResponse)
def aws_session_login_poll(login_id: str):
    with _LOGINS_LOCK:
        state = _LOGINS.get(login_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No se encontró el intento de login.")

    with state.lock:
        status = state.status
        message = state.message
        expires_at = state.expires_at
        profile = state.profile

    st = _status_for_profile(profile)
    if status == "authorized":
        st["active"] = True
        st["expires_at"] = expires_at or st.get("expires_at")
        st["message"] = message
    return {
        "login_id": login_id,
        "status": status,
        "active": bool(st.get("active")),
        "profile": profile,
        "expires_at": st.get("expires_at"),
        "message": message or st.get("message"),
    }