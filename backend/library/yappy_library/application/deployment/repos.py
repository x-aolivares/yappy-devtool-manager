"""El catálogo de repositorios del workspace, leído de Bitbucket.

La pantalla "Rama desplegada" necesita el nombre del repo para ofrecerlo mientras
se escribe, y para eso hace falta la lista completa del workspace. Esa lista es
barata de pedir una vez y cara de pedir en cada carga de la pantalla: el
listado de Bitbucket viene paginado (100 por página) y el input casi siempre se
llena con un nombre que el que escribe ya se sabe.

Este módulo sólo hace la llamada. **Dónde se guarda la lista y cuándo hay que
volver a preguntar** —SQLite, TTL de 24 h, refresh a pedido— es decisión de la
API: ver ``yappy_api/repo_catalog.py``. Acá no hay caché ni estado.

Se trae **todos** los repos del workspace, no sólo los de ECS/Fargate: filtrar
exigiría un request por repo para descartar los que no tienen
``.circleci/parameters.json``, que es mucho más caro que listar, y el que
elige un repo sin parameters.json igual recibe el aviso que ya existía.
"""

from __future__ import annotations

import json

from ...config import Config
from .ecs_branch import (
    BITBUCKET_API,
    DeploymentUnresolved,
    _http_get,
    bitbucket_workspace,
)

#: Tope de páginas al listar. Son 100 repos por página, así que 20 páginas son
#: 2000 repositorios — muy por encima del workspace real. El tope existe para
#: que un `next` que no avance no se convierta en un loop infinito, igual que
#: `_MAX_PIPELINE_PAGES` en `ecs_branch`.
_MAX_REPO_PAGES = 20

#: Repos por página. Es el máximo que acepta Bitbucket.
_PAGELEN = 100


def fetch_repositories(cfg=None) -> list[str]:
    """Todos los repos del workspace, ordenados alfabéticamente.

    El orden importa: es el que se muestra en el input, y que salga distinto
    entre máquinas o entre consultas haría parecer que cambió la lista.

    Tira `DeploymentUnresolved` —el mismo tipo que usa la cadena de deploy—
    cuando falta el token o Bitbucket no contesta. No reintenta: quién decide
    si conviene reintentar es el que guarda la caché.
    """
    cfg = cfg or Config()
    workspace = bitbucket_workspace(cfg)
    token = cfg.get("BITBUCKET_TOKEN")
    if not token:
        raise DeploymentUnresolved(
            "bitbucket_unavailable",
            "Falta BITBUCKET_TOKEN en la config para listar los repositorios "
            f"del workspace '{workspace}'.",
        )
    # El mismo esquema que `read_circleci_parameters`: Bearer y nada más.
    headers = {"Authorization": f"Bearer {token.strip()}", "Accept": "application/json"}

    names: list[str] = []
    url: str | None = (
        f"{BITBUCKET_API}/repositories/{workspace}"
        f"?pagelen={_PAGELEN}&sort=name"
    )
    for _ in range(_MAX_REPO_PAGES):
        if not url:
            break
        data = _as_object(_http_get(url, headers), url)
        for item in data.get("values", []):
            name = item.get("name") if isinstance(item, dict) else None
            if isinstance(name, str) and name:
                names.append(name)
        next_url = data.get("next")
        url = next_url if isinstance(next_url, str) else None

    # El set es por si el workspace cambió entre páginas y un repo se repite:
    # repetido en el input se vería como un bug del listado.
    return sorted({name for name in names}, key=str.lower)


def _as_object(body: str, url: str) -> dict:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DeploymentUnresolved(
            "bad_response", f"Bitbucket no devolvió JSON en {url}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DeploymentUnresolved(
            "bad_response", f"Bitbucket devolvió algo que no es un objeto en {url}."
        )
    return data
