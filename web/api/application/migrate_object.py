"""Caso de uso: migrar objetos de un ambiente hacia el MySQL local.

Solo DDL por ahora. El contrato (`MigrateRequest`) ya trae `mode`, `tables`,
`date_column`, `date_from` y `date_to` para que el incremento de "data por rango
de fechas" sea aditivo y no cambie esta firma.

Un fallo en un objeto no aborta el lote: se registra en `failures` y el resto
sigue, que es lo que espera alguien migrando media dozen de tablas.
"""
from __future__ import annotations

import re

from library.config import Config

from ..domain.entities import MigrateObject, MigrateRequest, MigrateResult
from ..domain.exceptions import MigrationError, UnsafeQueryError
from ..domain.ports import EnvironmentRepository, MigrateRepository
from .list_schemas import require_schema

_VALID_TARGET = re.compile(r"^[A-Za-z0-9_$]+$")

#: Kinds whose DDL we know how to extract.
SUPPORTED_KINDS = ("table", "view", "procedure", "function", "trigger")


def migrate_ddl(
    env_repo: EnvironmentRepository,
    repo: MigrateRepository,
    request: MigrateRequest,
) -> MigrateResult:
    if request.mode != "ddl":
        raise MigrationError(
            f"Unsupported migration mode '{request.mode}'. "
            f"Only 'ddl' is implemented; data-by-date-range is not yet available."
        )

    require_schema(env_repo, repo, request.environment, request.schema)

    objects = _resolve_objects(request)
    target = _resolve_target_schema(request)

    extracted = repo.extract_ddl(request.environment, request.schema, objects)
    return repo.apply_ddl(extracted, target)


def preview_ddl(
    env_repo: EnvironmentRepository,
    repo: MigrateRepository,
    request: MigrateRequest,
) -> list[MigrateObject]:
    """Extract DDL without applying it, so the UI can show what will run."""
    require_schema(env_repo, repo, request.environment, request.schema)
    return repo.extract_ddl(
        request.environment, request.schema, _resolve_objects(request)
    )


def _resolve_objects(request: MigrateRequest) -> tuple[tuple[str, str], ...]:
    if request.objects:
        objects = tuple(request.objects)
    elif request.tables:
        # Tables named without a kind are treated as base tables.
        objects = tuple(("table", t) for t in request.tables)
    else:
        raise MigrationError(
            "Nothing selected to migrate — pick at least one table, view, "
            "procedure, function or trigger."
        )

    for kind, name in objects:
        if kind not in SUPPORTED_KINDS:
            raise MigrationError(
                f"Cannot migrate kind '{kind}' — supported: "
                f"{', '.join(SUPPORTED_KINDS)}"
            )
        if not name:
            raise UnsafeQueryError("Object name cannot be empty")
    return objects


def _resolve_target_schema(request: MigrateRequest) -> str:
    """Where to write. Explicit wins, else LOCAL_DB_NAME, else `<env>_<schema>`."""
    if request.target_schema:
        candidate = request.target_schema
    else:
        configured = Config().get("LOCAL_DB_NAME")
        candidate = configured or f"{request.environment}_{request.schema}"

    if not _VALID_TARGET.match(candidate):
        raise MigrationError(
            f"Invalid target schema '{candidate}'. Use letters, digits and _ only."
        )
    return candidate
