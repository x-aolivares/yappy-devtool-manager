from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.list_environments import list_environments
from ..container import Container, get_container
from ..schemas import EnvironmentDTO

router = APIRouter(prefix="/environments", tags=["environments"])


@router.get("", response_model=list[EnvironmentDTO])
def get_environments(c: Container = Depends(get_container)) -> list[EnvironmentDTO]:
    environments = list_environments(c.environments)
    return [
        EnvironmentDTO(name=e.name, aws_profile=e.aws_profile, aws_region=e.aws_region)
        for e in environments
    ]
