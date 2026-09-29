from __future__ import annotations

from fastapi import APIRouter

from ..application.list_parameters import list_parameters
from ..infrastructure.env_config_adapter import EnvConfigAdapter
from ..infrastructure.param_config_adapter import EnvFileParameterAdapter
from ..schemas import ParameterDTO

router = APIRouter(prefix="/parameters", tags=["parameters"])

_env_repo = EnvConfigAdapter()
_param_repo = EnvFileParameterAdapter()


@router.get("/{env}", response_model=list[ParameterDTO])
def get_parameters(env: str) -> list[ParameterDTO]:
    parameters = list_parameters(_env_repo, _param_repo, env)
    return [
        ParameterDTO(key=p.key, value=p.value, is_json=p.is_json, environment=p.environment)
        for p in parameters
    ]
