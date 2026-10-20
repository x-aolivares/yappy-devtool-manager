"""Shared FastAPI dependencies for the web API."""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import HTTPException

from yappy_library.adapters.database.connection import SyncError
from yappy_library.application.database.sync.migrate import MigrationError
from yappy_library.application.database.sync.query import QueryError
from yappy_library.config import Config


def env_config(env: str) -> Config:
    """Resolve an environment name to its Config, with HTTP-friendly errors."""
    if env not in Config.known_environments():
        raise HTTPException(status_code=400, detail=f"Ambiente desconocido: '{env}'")
    try:
        return Config.with_env(env)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@contextmanager
def db_errors():
    """Mapear los fallos de la capa de base de datos a respuestas HTTP.

    Hay dos clases de fallo y se confunden:

    - ``SyncError`` es una **precondición**: el schema no está, la tabla no
      existe, la columna no es de fecha, el origen y el destino son el mismo.
      El pedido estaba mal y es un ``400``.
    - ``QueryError`` y ``MigrationError`` también lo son: lo pegado no es una
      sola lectura, o no se puede expandir en migraciones por tabla.
    - Cualquier otra excepción es **nuestro** bug o una falla del servidor. Un
      ``400`` la esconde: el cliente no reintenta, y un error de servidor que
      se ve como "pedido inválido" no aparece en ninguna métrica.

    Antes cada endpoint repetía el mismo par de ``except`` —diez veces en
    ``routes/db.py``— y los dos branches devolvían ``400``. Este context
    manager los junta y los distingue.

    El ``HTTPException`` se deja pasar: los rechazos por precondición que se
    lanzan dentro del bloque ya son ``400`` con su propio mensaje, y
    reempaquetarlos como "Error de base de datos" perdía ese mensaje.
    """
    try:
        yield
    except HTTPException:
        raise
    except SyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error de base de datos: {exc}") from exc
