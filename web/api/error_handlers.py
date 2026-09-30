"""Exception handlers centralizados — ningún error de dominio llega sin capturar al cliente."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .domain.exceptions import (
    AwsCredentialsError,
    ConfigKeyMissingError,
    DbConnectionError,
    DomainError,
    EnvironmentNotFoundError,
    LocalMysqlUnavailableError,
    MigrationError,
    ObjectNotFoundError,
    ParameterNotFoundError,
    SchemaNotFoundError,
    SecretNotFoundError,
    SecretResolutionError,
    UnsafeQueryError,
)

_STATUS_BY_EXCEPTION = {
    EnvironmentNotFoundError: 404,
    ParameterNotFoundError: 404,
    SchemaNotFoundError: 404,
    ObjectNotFoundError: 404,
    SecretNotFoundError: 404,
    AwsCredentialsError: 401,
    ConfigKeyMissingError: 500,
    SecretResolutionError: 502,
    DbConnectionError: 502,
    LocalMysqlUnavailableError: 503,
    MigrationError: 500,
    UnsafeQueryError: 400,
}


def _domain_error_response(exc: DomainError, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": exc.code, "message": str(exc), "detail": None},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status_code = _STATUS_BY_EXCEPTION.get(type(exc), 500)
        return _domain_error_response(exc, status_code)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "Unexpected server error",
                "detail": str(exc),
            },
        )
