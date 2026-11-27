"""Which branch is deployed in each environment, for an ECS/Fargate repository."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from yappy_library.application.deployment import ecs_branch, repos
from yappy_library.application.deployment.ecs_branch import DeploymentUnresolved
from yappy_library.config import Config

from .. import repo_catalog
from ..schemas import (
    DeploymentReposResponse,
    DeploymentsBranchesRequest,
    DeploymentsBranchesResponse,
)

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


def _repos_response(
    workspace: str, repositories: list[str], fetched_at: str | None, stale: bool
) -> DeploymentReposResponse:
    return DeploymentReposResponse(
        workspace=workspace,
        repositories=repositories,
        fetched_at=fetched_at,
        stale=stale,
    )


@router.get(
    "/api/deployments/repos",
    operation_id="list_deployment_repos",
    response_model=DeploymentReposResponse,
)
def api_deployment_repos(refresh: bool = False):
    """Todos los repos del workspace, para el input de "Rama desplegada".

    La lista se guarda en SQLite y se sirve desde ahí mientras tenga menos de
    24 h (`repo_catalog.TTL`), de modo que abrir la pantalla no paga el
    listado paginado de Bitbucket. `refresh=true` es el botón "Actualizar":
    manda a preguntar igual que si la caché no existiera, que es lo que hace
    visible un repo creado hace cinco minutos.

    Si hay caché y la consulta a Bitbucket falla, se sirve la caché vieja con
    `stale: true`: un input sin sugerencias es peor que una lista un poco
    desactualizada. Sin caché, el fallo es un 502 con el mensaje de la etapa.
    """
    cfg = Config()
    workspace = ecs_branch.bitbucket_workspace(cfg)
    cached = repo_catalog.read(workspace)
    if cached and not refresh and repo_catalog.is_fresh(cached["fetched_at"]):
        return _repos_response(
            workspace, cached["repositories"], cached["fetched_at"], stale=False
        )
    try:
        repositories = repos.fetch_repositories(cfg)
    except DeploymentUnresolved as exc:
        if not cached:
            raise HTTPException(status_code=502, detail=exc.message) from exc
        return _repos_response(
            workspace, cached["repositories"], cached["fetched_at"], stale=True
        )
    return _repos_response(
        workspace,
        repositories,
        repo_catalog.write(workspace, repositories),
        stale=False,
    )