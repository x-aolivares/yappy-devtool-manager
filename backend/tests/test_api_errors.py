"""Los fallos de la API tienen que decir qué clase de fallo son.

Tres cosas que se arreglan acá:

1. **Un error interno no es un pedido inválido.** Los endpoints de base de
   datos repetían diez veces el mismo par de ``except``, y los dos branches
   devolvían ``400``. Un ``TypeError`` nuestro —un bug— salía como "pedido
   inválido": el cliente no reintenta, y el error no aparece en ninguna
   métrica. Ahora ``SyncError`` es ``400`` y cualquier otra cosa es ``500``.

2. **Los rechazos que ya son 400 conservan su mensaje.** El
   ``except HTTPException: raise`` que había en uno de los endpoints existía
   justamente para eso, y el context manager lo hace por todos.

3. **SQLite no es del cliente.** La base de sesiones es local: que esté
   bloqueada o que el disco esté lleno es del servidor, y salía como un 500
   sin cuerpo.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from yappy_api.deps import db_errors
from yappy_api.routes.db import api_db_schemas
from yappy_library.adapters.database.connection import SyncError
from yappy_library.config import Config


class _FakeConfig:
    def __init__(self, env: str):
        self._env = env
        self.profile = "prof"
        self.region = "us-west-2"
        self.env = env


def _patch_env(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))


# --- db_errors -------------------------------------------------------------


def test_a_precondition_is_a_400():
    with pytest.raises(HTTPException) as exc_info:
        with db_errors():
            raise SyncError("El schema 'yappy' no existe en el origen.")

    assert exc_info.value.status_code == 400
    assert "no existe en el origen" in str(exc_info.value.detail)


def test_an_internal_error_is_a_500():
    """Un bug nuestro no puede verse como un pedido que el cliente rehaga."""
    with pytest.raises(HTTPException) as exc_info:
        with db_errors():
            raise TypeError("'NoneType' object is not subscriptable")

    assert exc_info.value.status_code == 500


def test_a_rejection_keeps_its_own_message():
    """Los rechazos por precondición ya son 400 con su propio texto."""
    with pytest.raises(HTTPException) as exc_info:
        with db_errors():
            raise HTTPException(
                status_code=400,
                detail="El destino la referencia con 2 foreign key(s): yappy.pedidos.",
            )

    assert exc_info.value.status_code == 400
    assert "foreign key" in str(exc_info.value.detail)


def test_the_endpoint_reports_an_internal_error_as_a_500(monkeypatch):
    _patch_env(monkeypatch)

    def _boom(cfg):
        raise TypeError("boom")

    monkeypatch.setattr("yappy_api.routes.db.connect", _boom)

    with pytest.raises(HTTPException) as exc_info:
        api_db_schemas("dev")

    assert exc_info.value.status_code == 500


def test_the_endpoint_still_reports_a_precondition_as_a_400(monkeypatch):
    _patch_env(monkeypatch)

    def _boom(cfg):
        raise SyncError("No hay ninguna base de datos alcanzable para este ambiente.")

    monkeypatch.setattr("yappy_api.routes.db.connect", _boom)

    with pytest.raises(HTTPException) as exc_info:
        api_db_schemas("dev")

    assert exc_info.value.status_code == 400
    assert "alcanzable" in str(exc_info.value.detail)


# --- SQLite ----------------------------------------------------------------


def test_a_locked_database_is_a_503_and_not_a_500():
    """La base de sesiones es local: que esté bloqueada es del servidor."""
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    client = TestClient(app, raise_server_exceptions=False)

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    import yappy_api.sessions as sessions_mod

    original = sessions_mod._connect
    sessions_mod._connect = _boom
    try:
        response = client.get("/api/sessions")
    finally:
        sessions_mod._connect = original

    assert response.status_code == 503
    assert "database is locked" in response.json()["detail"]


def test_a_duplicate_item_is_a_409_and_not_a_500():
    """El `UNIQUE(session_id, name)` es un conflicto, no una falla del server."""
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    client = TestClient(app, raise_server_exceptions=False)

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: items.session_id")

    import yappy_api.sessions as sessions_mod

    original = sessions_mod._connect
    sessions_mod._connect = _boom
    try:
        response = client.get("/api/sessions")
    finally:
        sessions_mod._connect = original

    assert response.status_code == 409


# --- el contrato de la entrada ---------------------------------------------


def test_un_value_type_invalido_es_un_422():
    """`value_type` es un enum, no un texto libre.

    El valor terminaba en un comando de shell que la UI le muestra al developer
    para que lo corra. Con el tipo libre, un `;` en el medio del comando lo
    convertía en dos comandos.
    """
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    client = TestClient(app)

    response = client.post(
        "/api/params/apply",
        json={
            "env_a": "dev",
            "env_b": "qa",
            "service": "ssm",
            "name": "/x",
            "new_value": "20",
            "value_type": "String; rm -rf ~",
        },
    )

    assert response.status_code == 422


def test_los_value_type_validos_pasan():
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    client = TestClient(app)

    for value_type in ("String", "StringList", "SecureString"):
        response = client.post(
            "/api/params/apply",
            json={
                "env_a": "dev",
                "env_b": "qa",
                "service": "ssm",
                "name": "/x",
                "new_value": "20",
                "value_type": value_type,
            },
        )
        assert response.status_code != 422, f"{value_type} fue rechazado"
