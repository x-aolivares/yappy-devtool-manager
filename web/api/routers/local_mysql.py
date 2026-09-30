from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.local_mysql import local_start, local_status, local_stop
from ..container import Container, get_container
from ..schemas import LocalMysqlStatusDTO

router = APIRouter(prefix="/local-mysql", tags=["local-mysql"])


def _to_dto(status) -> LocalMysqlStatusDTO:
    return LocalMysqlStatusDTO(
        running=status.running,
        host=status.host,
        port=status.port,
        user=status.user,
        detail=status.detail,
        start_command=status.start_command,
    )


@router.get("", response_model=LocalMysqlStatusDTO)
def get_status(c: Container = Depends(get_container)) -> LocalMysqlStatusDTO:
    return _to_dto(local_status(c.local_mysql))


@router.post("/start", response_model=LocalMysqlStatusDTO)
def post_start(c: Container = Depends(get_container)) -> LocalMysqlStatusDTO:
    """Start the local MySQL and verify the configured credentials work."""
    return _to_dto(local_start(c.local_mysql))


@router.post("/stop", response_model=LocalMysqlStatusDTO)
def post_stop(c: Container = Depends(get_container)) -> LocalMysqlStatusDTO:
    return _to_dto(local_stop(c.local_mysql))
