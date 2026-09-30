"""Caso de uso: listar tablas, vistas, SPs, funciones y triggers de un schema."""
from __future__ import annotations

from ..domain.entities import DbObject
from ..domain.ports import DbRepository
from .list_schemas import require_schema


def list_objects(
    env_repo,
    db_repo: DbRepository,
    env: str,
    schema: str,
    kind: str | None = None,
) -> list[DbObject]:
    """`kind` filters to one type; None returns everything, grouped by kind."""
    require_schema(env_repo, db_repo, env, schema)
    objects = db_repo.list_objects(env, schema)
    if kind:
        wanted = kind.lower()
        return [o for o in objects if o.kind == wanted]
    return objects
