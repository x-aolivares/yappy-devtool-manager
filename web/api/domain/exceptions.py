"""Excepciones de dominio. Cada una se mapea a un HTTP status en error_handlers.py."""
from __future__ import annotations


class DomainError(Exception):
    """Base de toda excepción de dominio de la web app."""
    code = "DOMAIN_ERROR"


class EnvironmentNotFoundError(DomainError):
    code = "ENVIRONMENT_NOT_FOUND"

    def __init__(self, env: str, known: list[str]):
        self.env = env
        self.known = known
        super().__init__(
            f"Environment '{env}' not found. Available: {', '.join(known) or 'none'}"
        )


class ParameterNotFoundError(DomainError):
    code = "PARAMETER_NOT_FOUND"

    def __init__(self, key: str, env: str):
        self.key = key
        self.env = env
        super().__init__(f"Parameter '{key}' not found in environment '{env}'")


class SecretResolutionError(DomainError):
    code = "SECRET_RESOLUTION_ERROR"


class DbConnectionError(DomainError):
    code = "DB_CONNECTION_ERROR"


class SchemaNotFoundError(DomainError):
    code = "SCHEMA_NOT_FOUND"


class LocalMysqlUnavailableError(DomainError):
    code = "LOCAL_MYSQL_UNAVAILABLE"


class UnsafeQueryError(DomainError):
    code = "UNSAFE_QUERY"
