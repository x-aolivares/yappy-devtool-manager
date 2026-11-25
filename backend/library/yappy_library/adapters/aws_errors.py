"""Detección de errores de sesión/credenciales AWS.

Se usa tanto desde la library como desde la API web para traducir errores
crudos de botocore a una señal clara de "sesión AWS vencida/no autenticada".
"""

from __future__ import annotations

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

_SSO_MESSAGE_HINTS = (
    "token has expired and refresh failed",
    "the sso session associated with this profile has expired",
    "to refresh this sso session run aws sso login",
    "error when retrieving token from sso",
    "expiredtoken",
)


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
        msg = str(current).lower()
        if "sso" in name.lower() and "token" in msg:
            return True
        if any(hint in msg for hint in _SSO_MESSAGE_HINTS):
            return True
        if "sso session" in msg and any(word in msg for word in ("expired", "login", "token")):
            return True
    return False


class AwsSessionExpiredError(RuntimeError):
    """La sesión AWS/SSO no está activa o venció."""

    def __init__(self, message: str = "La sesión de AWS no está activa o venció.", *, profile: str | None = None):
        super().__init__(message)
        self.profile = profile