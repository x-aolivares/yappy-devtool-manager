"""Caso de uso: schemas disponibles en un ambiente (para el drop-down)."""
from __future__ import annotations

from ..domain.entities import Schema
from ..domain.exceptions import EnvironmentNotFoundError
from ..domain.ports import DbRepository, EnvironmentRepository


def list_schemas(
    env_repo: EnvironmentRepository, db_repo: DbRepository, env: str
) -> list[Schema]:
    _require_env(env_repo, env)
    return db_repo.list_schemas(env)


def require_schema(
    env_repo: EnvironmentRepository, db_repo: DbRepository, env: str, schema: str
) -> str:
    """Validate that `schema` exists in `env`, returning it normalised."""
    from ..domain.exceptions import SchemaNotFoundError

    known = {s.name for s in list_schemas(env_repo, db_repo, env)}
    if schema not in known:
        raise SchemaNotFoundError(
            f"Schema '{schema}' not found in '{env}'. "
            f"Available: {', '.join(sorted(known)) or 'none'}"
        )
    return schema


def _require_env(env_repo: EnvironmentRepository, env: str) -> None:
    known = [e.name for e in env_repo.list_environments()]
    if env not in known:
        raise EnvironmentNotFoundError(env, known)
