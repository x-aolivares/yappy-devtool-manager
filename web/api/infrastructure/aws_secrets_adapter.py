"""Adapter de Secrets Manager.

Resuelve UN secreto por nombre usando el profile/región del ambiente. El nombre
del secreto es, literalmente, el valor crudo del parámetro de `config/env.<env>`
(ver `resolve_secret` en application/). No se lista ni se buscan secretos: solo
se hace `GetSecretValue` cuando el usuario lo pide explícitamente.

Usa `botocore` (no boto3) porque es lo que ya depende el toolkit y porque
`library/db/tunnel.py` genera el token RDS con el mismo patrón. Ningún error
de AWS escapa como excepción de botocore: todo se traduce a excepciones de
dominio, porque ninguna debe llegar cruda al cliente.
"""
from __future__ import annotations

import base64
import json

import botocore.session
from botocore.exceptions import BotoCoreError, ClientError

from library.config import Config

from ..domain.entities import ResolvedSecret
from ..domain.exceptions import (
    AwsCredentialsError,
    EnvironmentNotFoundError,
    SecretNotFoundError,
    SecretResolutionError,
)

_NOT_FOUND_CODES = {"ResourceNotFoundException"}
_AUTH_CODES = {
    "UnrecognizedClientException",
    "AccessDeniedException",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "UnauthorizedException",
}


class AwsSecretsAdapter:
    """Implementa SecretResolver sobre Secrets Manager."""

    def resolve(self, env: str, secret_name: str) -> ResolvedSecret:
        secret_name = (secret_name or "").strip()
        if not secret_name:
            raise SecretResolutionError("Empty secret name — nothing to resolve")

        cfg = self._config_for(env)
        client = self._client(cfg)
        value = self._get_secret_value(client, secret_name, env)
        return ResolvedSecret(
            secret_name=secret_name,
            value=value,
            environment=env,
            is_json=_looks_like_json(value),
        )

    # --- internals -------------------------------------------------------

    @staticmethod
    def _config_for(env: str) -> Config:
        known = Config.known_environments()
        if env not in known:
            raise EnvironmentNotFoundError(env, known)
        return Config.with_env(env)

    @staticmethod
    def _client(cfg: Config):
        try:
            session = botocore.session.Session(profile=cfg.profile)
            return session.create_client("secretsmanager", region_name=cfg.region)
        except BotoCoreError as e:
            raise AwsCredentialsError(
                f"Cannot create Secrets Manager client for profile "
                f"'{cfg.profile}' in '{cfg.region}': {e}"
            ) from e
        except Exception as e:  # profile inexistente, config corrupta, etc.
            raise AwsCredentialsError(
                f"Cannot create Secrets Manager client for profile "
                f"'{cfg.profile}': {e}. Try 'yappy login aws'."
            ) from e

    @staticmethod
    def _get_secret_value(client, secret_name: str, env: str) -> str:
        try:
            response = client.get_secret_value(SecretId=secret_name)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _NOT_FOUND_CODES:
                raise SecretNotFoundError(secret_name, env) from e
            if code in _AUTH_CODES:
                raise AwsCredentialsError(
                    f"AWS rejected the request for '{secret_name}' ({code}). "
                    f"Your session may have expired — run 'yappy login aws'."
                ) from e
            raise SecretResolutionError(
                f"Could not resolve secret '{secret_name}' in '{env}': "
                f"{code or type(e).__name__}: {e}"
            ) from e
        except BotoCoreError as e:
            # Timeout / credenciales no resolubles / red
            raise SecretResolutionError(
                f"Could not reach Secrets Manager for '{secret_name}' in '{env}': {e}"
            ) from e

        return _extract_value(response, secret_name)


def _extract_value(response: dict, secret_name: str) -> str:
    """Secrets Manager devuelve SecretString, o SecretBinary si es binario."""
    value = response.get("SecretString")
    if value is not None:
        return value
    binary = response.get("SecretBinary")
    if binary is not None:
        try:
            return base64.b64decode(binary).decode("utf-8", errors="replace")
        except Exception as e:
            raise SecretResolutionError(
                f"Secret '{secret_name}' is binary and could not be decoded: {e}"
            ) from e
    raise SecretResolutionError(
        f"Secret '{secret_name}' returned neither SecretString nor SecretBinary"
    )


def _looks_like_json(value: str) -> bool:
    try:
        json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    return True
