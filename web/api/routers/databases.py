from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.list_objects import list_objects
from ..application.list_schemas import list_schemas
from ..application.local_mysql import probe_environment
from ..container import Container, get_container
from ..schemas import ConnectionInfoDTO, DbObjectDTO, SchemaDTO

router = APIRouter(prefix="/databases", tags=["databases"])


@router.get("/{env}/schemas", response_model=list[SchemaDTO])
def get_schemas(env: str, c: Container = Depends(get_container)) -> list[SchemaDTO]:
    return [
        SchemaDTO(name=s.name, environment=s.environment)
        for s in list_schemas(c.environments, c.mysql, env)
    ]


@router.get("/{env}/schemas/{schema}/objects", response_model=list[DbObjectDTO])
def get_objects(
    env: str,
    schema: str,
    kind: str | None = None,
    c: Container = Depends(get_container),
) -> list[DbObjectDTO]:
    objects = list_objects(c.environments, c.mysql, env, schema, kind)
    return [
        DbObjectDTO(name=o.name, kind=o.kind, db_schema=o.schema) for o in objects
    ]


@router.get("/{env}/connection", response_model=ConnectionInfoDTO)
def get_connection(env: str, c: Container = Depends(get_container)) -> ConnectionInfoDTO:
    """Is the environment's DB tunnel up? A closed port is a rendered state,
    not an error, so this endpoint never 502s."""
    info = probe_environment(c.mysql, env)
    return ConnectionInfoDTO(
        target=info.target,
        host=info.host,
        port=info.port,
        user=info.user,
        reachable=info.reachable,
        detail=info.detail,
    )
