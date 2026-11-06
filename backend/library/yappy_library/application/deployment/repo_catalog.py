"""Los repositorios del workspace de Bitbucket a los que el token tiene acceso.

Se bajan todos de una vez, paginando, y se guardan **en memoria del proceso**. La
pantalla filtra esa lista mientras se escribe, así que no hay una consulta a
Bitbucket por cada tecla.

No hay SQLite a propósito: son unos cientos de nombres, se vuelven a bajar en
segundos y no hace falta conservarlos entre reinicios. La lista vieja dura
``CATALOG_TTL_SECONDS``; después de eso la siguiente lectura la vuelve a bajar.
``refresh=True`` la fuerza.

Si la actualización falla, la lista anterior no se borra: la respuesta es el
error, y la copia en memoria sigue siendo la última que se bajó bien.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from ...config import Config
from .ecs_branch import BITBUCKET_API, DeploymentUnresolved, _http_get, bitbucket_workspace

#: Cuánto vale una lista bajada antes de volver a pedirla.
CATALOG_TTL_SECONDS = 3600

#: Repos por página en Bitbucket. El máximo que acepta el endpoint.
PAGE_LEN = 100

#: Tope de páginas: el tope existe para que un `next` que no avanza no sea un loop.
MAX_PAGES = 50


@dataclass(frozen=True)
class RepoInfo:
    """Lo que la pantalla necesita de un repo para mostrarlo y elegirlo."""

    slug: str
    name: str
    full_name: str
    updated_on: str | None
    is_private: bool


@dataclass(frozen=True)
class CatalogSnapshot:
    workspace: str
    fetched_at: float
    repos: tuple[RepoInfo, ...]


#: Un workspace -> (cuándo se bajó, la lista). Un solo lock: la lista se baja de a una
#: vez y dos pedidos concurrentes no deben bajarla dos veces.
_cache: dict[str, tuple[float, tuple[RepoInfo, ...]]] = {}
_cache_lock = threading.Lock()


def _repo_from(value: dict[str, Any]) -> RepoInfo | None:
    slug = value.get("slug")
    if not isinstance(slug, str) or not slug:
        return None
    return RepoInfo(
        slug=slug,
        name=value.get("name") or slug,
        full_name=value.get("full_name") or "",
        updated_on=value.get("updated_on"),
        is_private=bool(value.get("is_private")),
    )


def _fetch_all(workspace: str, token: str) -> tuple[RepoInfo, ...]:
    """Todas las páginas de ``/repositories/<workspace>``, en el orden de Bitbucket."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url: str | None = f"{BITBUCKET_API}/repositories/{workspace}?pagelen={PAGE_LEN}"
    repos: list[RepoInfo] = []
    for _ in range(MAX_PAGES):
        if url is None:
            break
        try:
            body = _http_get(url, headers)
        except DeploymentUnresolved as exc:
            if exc.status == "http_error" and " 401" in exc.message:
                raise DeploymentUnresolved(
                    "bitbucket_unauthorized",
                    "Bitbucket rechazó el token (401). Verificá que BITBUCKET_TOKEN siga "
                    "vigente y que no tenga espacios copiados de más.",
                ) from exc
            raise
        try:
            page = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DeploymentUnresolved(
                "bad_response", f"Bitbucket devolvió algo que no es JSON: {exc}"
            ) from exc
        for value in page.get("values", []):
            repo = _repo_from(value)
            if repo is not None:
                repos.append(repo)
        url = page.get("next")
    else:
        # Salió por el tope sin llegar a la última página: no es una lista completa,
        # y mostrarla como si lo fuera escondería repos.
        raise DeploymentUnresolved(
            "bad_response",
            f"El workspace {workspace} tiene más de {MAX_PAGES * PAGE_LEN} repos; "
            "la lista quedaría incompleta.",
        )
    return tuple(sorted(repos, key=lambda r: r.slug))


def list_repos(cfg: Config, *, refresh: bool = False) -> CatalogSnapshot:
    """La lista del workspace: la de memoria si todavía vale, o una nueva.

    Lanza :class:`DeploymentUnresolved` si falta el token o Bitbucket falla. La ruta
    lo traduce a un 502 con el mensaje tal cual.
    """
    workspace = bitbucket_workspace(cfg)
    token = cfg.get("BITBUCKET_TOKEN")
    if not token:
        raise DeploymentUnresolved(
            "bitbucket_unavailable",
            "Falta BITBUCKET_TOKEN en la config para listar los repositorios.",
        )
    with _cache_lock:
        cached = _cache.get(workspace)
        if cached and not refresh and time.time() - cached[0] < CATALOG_TTL_SECONDS:
            return CatalogSnapshot(workspace, cached[0], cached[1])
        repos = _fetch_all(workspace, token.strip())
        fetched_at = time.time()
        _cache[workspace] = (fetched_at, repos)
        return CatalogSnapshot(workspace, fetched_at, repos)
