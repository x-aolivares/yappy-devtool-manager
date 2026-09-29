from __future__ import annotations

from fastapi import APIRouter

from ..application.list_environments import list_environments
from ..infrastructure.env_config_adapter import EnvConfigAdapter
from ..schemas import EnvironmentDTO

router = APIRouter(prefix="/environments", tags=["environments"])

_env_repo = EnvConfigAdapter()


@router.get("", response_model=list[EnvironmentDTO])
def get_environments() -> list[EnvironmentDTO]:
    environments = list_environments(_env_repo)
    return [
        EnvironmentDTO(name=e.name, aws_profile=e.aws_profile, aws_region=e.aws_region)
        for e in environments
    ]
