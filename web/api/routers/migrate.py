from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.migrate_object import migrate_ddl, preview_ddl
from ..container import Container, get_container
from ..domain.entities import MigrateRequest
from ..schemas import MigrateObjectDTO, MigrateRequestDTO, MigrateResultDTO

router = APIRouter(prefix="/databases", tags=["migrate"])


def _to_request(env: str, body: MigrateRequestDTO) -> MigrateRequest:
    return MigrateRequest(
        environment=env,
        schema=body.db_schema,
        mode=body.mode,
        objects=tuple(tuple(o) for o in body.objects),  # type: ignore[misc]
        tables=tuple(body.tables),
        date_column=body.date_column,
        date_from=body.date_from,
        date_to=body.date_to,
        target_schema=body.target_schema,
    )


def _obj_dto(o) -> MigrateObjectDTO:
    return MigrateObjectDTO(
        kind=o.kind,
        db_schema=o.schema,
        name=o.name,
        ddl=o.ddl,
        environment=o.environment,
        warnings=list(o.warnings),
    )


@router.post("/{env}/migrate/preview", response_model=list[MigrateObjectDTO])
def post_preview(
    env: str,
    body: MigrateRequestDTO,
    c: Container = Depends(get_container),
) -> list[MigrateObjectDTO]:
    """Show the DDL that would run, without touching the local MySQL."""
    objects = preview_ddl(c.environments, c.migrate, _to_request(env, body))
    return [_obj_dto(o) for o in objects]


@router.post("/{env}/migrate", response_model=MigrateResultDTO)
def post_migrate(
    env: str,
    body: MigrateRequestDTO,
    c: Container = Depends(get_container),
) -> MigrateResultDTO:
    """Extract DDL from the environment and apply it to the local MySQL.

    Per-object failures are collected instead of aborting the batch.
    """
    result = migrate_ddl(c.environments, c.migrate, _to_request(env, body))
    return MigrateResultDTO(
        target_schema=result.target_schema,
        statements_executed=result.statements_executed,
        statements_failed=len(result.failures),
        applied=[_obj_dto(o) for o in result.applied],
        failures=list(result.failures),
    )
