"""Which branch is deployed in each environment, for an ECS/Fargate repository."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from yappy_library.application.deployment import ecs_branch, repo_catalog
from yappy_library.config import Config

from ..schemas import DeploymentReposResponse, DeploymentsBranchesRequest, DeploymentsBranchesResponse

router = APIRouter(tags=["deployments"])


def _aws_environments(only: list[str] | None = None) -> list[str]:
    """The environments worth asking ECS about.

    `local` is left out on purpose: it has no ECS, so asking it would spend a call
    to learn something the config already says. An environment whose config fails
    to load stays in the list, because then the row says why instead of the
    environment silently vanishing from the table.

    `only` narrows it to what el usuario pidió mirar. Cada fila es un
    `describe_task_definition` en su región más uno o dos requests a CircleCI, así
    que filtrar acá es lo que hace que mirar una región no cueste el viaje a las
    otras. Un ambiente pedido que no está en la configuración entra igual: la fila
    lo dice con un error, que es más honesto que ignorarlo en silencio.
    """
    available = [env for env in Config.known_environments() if not _is_local(env)]
    if not only:
        return available
    known = set(available)
    return [env for env in only if env in known or not _is_local(env)]


def _is_local(env: str) -> bool:
    try:
        return bool(Config.with_env(env).is_local)
    except Exception:
        return False


@router.get(
    "/api/deployments/repos",
    operation_id="list_deployment_repos",
    response_model=DeploymentReposResponse,
)
def api_deployment_repos(refresh: bool = False):
    """Todos los repos del workspace de Bitbucket a los que el token tiene acceso.

    La lista se guarda en memoria una hora. ``refresh=true`` la vuelve a bajar.
    """
    try:
        snapshot = repo_catalog.list_repos(Config(), refresh=refresh)
    except ecs_branch.DeploymentUnresolved as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    return {
        "workspace": snapshot.workspace,
        "fetched_at": snapshot.fetched_at,
        "count": len(snapshot.repos),
        "repos": [
            {
                "slug": repo.slug,
                "name": repo.name,
                "full_name": repo.full_name,
                "updated_on": repo.updated_on,
                "is_private": repo.is_private,
            }
            for repo in snapshot.repos
        ],
    }


@router.post(
    "/api/deployments/branches",
    operation_id="list_deployment_branches",
    response_model=DeploymentsBranchesResponse,
)
def api_deployment_branches(request: DeploymentsBranchesRequest):
    """Walk repo -> parameters.json -> ECS -> CircleCI for each environment asked."""
    try:
        return ecs_branch.deployment_report(request.repo, _aws_environments(request.envs))
    except ecs_branch.DeploymentUnresolved as exc:
        # The repository itself could not be read, so there is no table to draw:
        # every row would carry the same message. That is a failure of the
        # request, not of one environment.
        raise HTTPException(status_code=502, detail=exc.message) from exc