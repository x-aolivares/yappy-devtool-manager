"""Helpers para detectar y exponer el estado de sesión AWS/SSO.

La web corre local, pero muchas acciones dependen de credenciales AWS válidas.
Cuando la sesión SSO vence, el error crudo de botocore no le dice mucho al
usuario; acá lo traducimos a una señal clara que el front puede reconocer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

AWS_SESSION_ERROR_CODE = "aws_session_expired"

_SSO_ERROR_NAMES = {
    "UnauthorizedSSOTokenError",
    "TokenRetrievalError",
    "SSOTokenLoadError",
    "CredentialRetrievalError",
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
}


def _iter_exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        next_exc = current.__cause__ or current.__context__
        current = next_exc if isinstance(next_exc, BaseException) else None


def is_aws_session_error(exc: BaseException) -> bool:
    for current in _iter_exception_chain(exc):
        name = current.__class__.__name__
        if name in _SSO_ERROR_NAMES:
            return True
        if "SSO" in name and "token" in str(current).lower():
            return True
        msg = str(current).lower()
        if "token has expired and refresh failed" in msg:
            return True
        if "sso session" in msg and ("expired" in msg or "login" in msg or "token" in msg):
            return True
        if "the sso session associated with this profile has expired" in msg:
            return True
        if "to refresh this sso session run aws sso login" in msg:
            return True
        if "error when retrieving token from sso" in msg:
            return True
    return False


def aws_session_http_exception(profile: str | None = None) -> HTTPException:
    detail = {
        "code": AWS_SESSION_ERROR_CODE,
        "message": "La sesión de AWS no está activa o venció. Iniciá sesión para continuar.",
        "profile": profile,
    }
    return HTTPException(status_code=401, detail=detail)


def _parse_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        # AWS cache usually uses Z suffix.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def read_sso_cache(cache_path) -> dict[str, Any] | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def cache_status(cache_path, *, profile: str, sso_session: str | None = None, start_url: str | None = None) -> dict[str, Any]:
    data = read_sso_cache(cache_path)
    if not data:
        return {
            "active": False,
            "profile": profile,
            "sso_session": sso_session,
            "start_url": start_url,
            "expires_at": None,
            "expires_in_seconds": None,
            "message": "No hay una sesión SSO cacheada.",
        }

    expires_at = _parse_expires_at(data.get("expiresAt") or data.get("expires_at"))
    now = datetime.now(timezone.utc)
    # El front descuenta este número desde que recibió la respuesta; mandar un
    # negativo lo obligaría a adivinar cuánto hace que venció. Acá sólo importa
    # que ya venció, así que se informa 0.
    expires_in = max(0, int((expires_at - now).total_seconds())) if expires_at else None
    active = bool(data.get("accessToken")) and expires_in is not None and expires_in > 0
    return {
        "active": active,
        "profile": profile,
        "sso_session": sso_session or data.get("sessionName"),
        "start_url": start_url or data.get("startUrl"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z") if expires_at else data.get("expiresAt"),
        "expires_in_seconds": expires_in if expires_in is not None else None,
        "message": None if active else "La sesión de AWS venció o no está activa.",
    }
