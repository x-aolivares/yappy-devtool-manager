from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.list_parameters import list_parameters
from ..application.resolve_secret import resolve_secret
from ..container import Container, get_container
from ..schemas import ParameterDTO, ResolvedSecretDTO, ResolveSecretRequest

router = APIRouter(prefix="/parameters", tags=["parameters"])


@router.get("/{env}", response_model=list[ParameterDTO])
def get_parameters(env: str, c: Container = Depends(get_container)) -> list[ParameterDTO]:
    parameters = list_parameters(c.environments, c.parameters, env)
    return [
        ParameterDTO(key=p.key, value=p.value, is_json=p.is_json, environment=p.environment)
        for p in parameters
    ]


@router.post(
    "/{env}/resolve",
    response_model=ResolvedSecretDTO,
    summary="Resolve a parameter's value in Secrets Manager",
)
def resolve(
    env: str,
    body: ResolveSecretRequest,
    c: Container = Depends(get_container),
) -> ResolvedSecretDTO:
    """Resolve the raw parameter value as a Secrets Manager secret name.

    Only ever called on explicit user action — listing parameters never
    touches AWS.
    """
    resolved = resolve_secret(c.environments, c.parameters, c.secrets, env, body.key)
    return ResolvedSecretDTO(
        secret_name=resolved.secret_name,
        value=resolved.value,
        is_json=resolved.is_json,
        environment=resolved.environment,
    )
