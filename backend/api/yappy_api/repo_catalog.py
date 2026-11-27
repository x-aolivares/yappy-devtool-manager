"""Catálogo de repositorios del workspace (SQLite, local sólo).

Guarda la lista de nombres que el input de "Rama desplegada" ofrece mientras se
escribe, para no pagar el listado paginado de Bitbucket en cada carga de la
pantalla. Es un caché de un solo dato —una lista de strings—, no un ledger:
qué cambia y quién lo cambia está afuera, en Bitbucket.

**Cuándo se vuelve a consultar** se decide acá y en ningún otro lado:

- ``TTL`` — con menos de 24 h, la caché se sirve sin tocar la red.
- ``refresh=True`` — el botón "Actualizar" de la pantalla, que manda a preguntar
  igual que si la caché no existiera. Sirve para el repo creado hace cinco
  minutos, que el TTL no iba a mostrar hasta mañana.

La copia en `localStorage` del frontend no decide nada: es sólo el pintado
instantáneo y el respaldo si este backend no está. Si las dos difieren, gana
esta, que es la que se acaba de leer de Bitbucket.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from yappy_library.paths import migrate_backend_resources, project_root

#: Cuánto vive una lista antes de volver a preguntarle a Bitbucket.
#:
#: Un repo nuevo tarda hasta esto en aparecer sin apretar "Actualizar", que es
#: el intercambio aceptado: el listado completo del workspace no cambia en el
#: día, y el viaje paginado se paga en cada carga de la pantalla si no.
TTL = timedelta(hours=24)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_catalog (
    workspace    TEXT PRIMARY KEY,
    repositories TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);
"""


def _db_path() -> Path:
    override = os.environ.get("YAPPY_REPO_CATALOG_DB")
    if override:
        return Path(override)
    root = project_root()
    if (root / "backend").is_dir():
        migrate_backend_resources(root, names=("data",))
        return root / "backend" / "data" / "repo_catalog.db"
    return root / "data" / "repo_catalog.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now()


def read(workspace: str) -> dict | None:
    """`{"repositories": [...], "fetched_at": iso}` o `None` si nunca se consultó.

    `None` es "no hay caché", que no es un error: es la señal para ir a
    preguntar. Una caché vencida se devuelve igual — decide el que llama.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT repositories, fetched_at FROM repo_catalog WHERE workspace = ?",
            (workspace,),
        ).fetchone()
    if row is None:
        return None
    try:
        repositories = json.loads(row["repositories"])
    except json.JSONDecodeError:
        # Una caché ilegible es una caché inexistente, no un 500: se vuelve a
        # consultar y se reescribe.
        return None
    if not isinstance(repositories, list):
        return None
    return {"repositories": repositories, "fetched_at": row["fetched_at"]}


def write(workspace: str, repositories: list[str]) -> str:
    """Guarda la lista y devuelve el `fetched_at` en ISO, que es lo que se
    devuelve en la respuesta y con lo que después se mide el vencimiento."""
    fetched_at = _now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO repo_catalog (workspace, repositories, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(workspace) DO UPDATE SET
                repositories = excluded.repositories,
                fetched_at   = excluded.fetched_at
            """,
            (workspace, json.dumps(repositories), fetched_at),
        )
    return fetched_at


def is_fresh(fetched_at: str, now: datetime | None = None) -> bool:
    """¿La caché tiene menos de `TTL`?

    Un `fetched_at` que no se puede leer cuenta como vencido: es más seguro
    pagar el viaje que servir una fecha que no se entiende.
    """
    try:
        fetched = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return False
    return (now or _now()) - fetched < TTL
