"""Los contracts de los endpoints, servidos de verdad por la app.

Los tests de `test_workflow_api.py` llaman los handlers directo. Eso es rápido y
lee bien, pero **se saltea la validación de respuesta de FastAPI**: el
`response_model` no se ejecuta y un desajuste entre lo que devuelve el handler y
lo que declara el schema no se ve.

Y así llegó a la pantalla un 500: `stop_entry` devuelve `stopped: bool` —"¿lo
maté?"— y el schema pedía `stopped: list[int]`, porque compartía modelo con
`stop-all`, que sí devuelve una lista. Los 617 tests pasaban.

Estos tests pasan por el `TestClient`, que sí serializa y valida.
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.adapters.processes import BaseCommand


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


def _track(tracker, pid, **overrides):
    entry = {
        "pid": pid,
        "resource": "tunnel",
        "target": "base-profile",
        "env": "dev",
        "started_at": 1757000000.0,
        "log_file": "",
        "local_port": None,
        "remote_port": None,
        "remote_host": "",
        "cluster": "cap",
        "label": "",
        "owner": "web",
        "state": "up",
        **overrides,
    }
    tracker.mkdir(parents=True, exist_ok=True)
    (tracker / f"{pid}.json").write_text(json.dumps(entry))


@contextlib.contextmanager
def recorder():
    killed: list[int] = []
    original = BaseCommand.kill_ssm
    BaseCommand.kill_ssm = lambda self, pid=None: killed.append(pid)
    try:
        yield killed
    finally:
        BaseCommand.kill_ssm = original


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    return TestClient(app)


def test_parar_uno_devuelve_un_booleano(client, tracker):
    """El bug: `stopped` es un bool acá y una lista en `stop-all`.

    Con el modelo compartido, esto era un 500 en la pantalla."""
    pid = os.getpid()
    _track(tracker, pid, local_port=8402)

    with recorder():
        response = client.post("/api/workflow/stop", json={"pid": pid})

    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "pid": pid, "stopped": True}


def test_parar_todo_devuelve_una_lista(client, tracker):
    """La forma distinta, que el mismo schema no podía representar."""
    _track(tracker, os.getpid(), local_port=8402)
    _track(tracker, 4242, alive=False, local_port=8403)

    with recorder() as killed:
        response = client.post("/api/workflow/stop-all")

    assert response.status_code == 200
    body = response.json()
    assert body["stopped"] == [os.getpid()]
    assert body["refused"] == []
    assert killed == [os.getpid()]


def test_parar_todo_con_ajenos_devuelve_la_lista_de_refusados(client, tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, owner="cli")

    with recorder():
        body = client.post("/api/workflow/stop-all").json()

    assert body["stopped"] == []
    assert [r["pid"] for r in body["refused"]] == [pid]


def test_parar_un_proceso_ajeno_es_409_con_el_motivo(client, tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, owner="cli")

    with recorder() as killed:
        response = client.post("/api/workflow/stop", json={"pid": pid})

    assert response.status_code == 409
    assert "no lo levantó la web" in response.json()["detail"]
    assert killed == []


def test_parar_algo_que_no_esta_trackeado_es_409(client, tracker):
    response = client.post("/api/workflow/stop", json={"pid": 999999})

    assert response.status_code == 409
    assert "no está trackeado" in response.json()["detail"]


def test_el_estado_trae_catalog_configured(client):
    """Sin esto, "el cluster no tiene puertos" y "nadie configuró el catálogo"
    se ven idénticos y se arreglan distinto."""
    body = client.get("/api/workflow/state", params={"env": "dev", "cluster": "cap"}).json()

    assert "catalog_configured" in body


def test_un_cluster_desconocido_es_400(client):
    response = client.get("/api/workflow/state", params={"env": "dev", "cluster": "nope"})

    assert response.status_code == 400
    assert "cap" in response.json()["detail"]


def test_arrancar_un_recurso_devuelve_202(client, monkeypatch):
    from yappy_api.routes import workflow

    monkeypatch.setattr(
        workflow.actions,
        "start_resource",
        lambda cfg, key: {"pid": 4242, "port": 9092, "state": "starting", "label": key},
    )

    response = client.post(
        "/api/workflow/resources/start", json={"key": "kafka-server"}, params={"env": "dev"}
    )

    assert response.status_code == 202
    assert response.json()["state"] == "starting"


def test_un_start_rechazado_es_409(client, monkeypatch):
    from yappy_api.routes import workflow
    from yappy_library.application.workflow.actions import StartRefused

    def refuse(cfg, cluster, port, label=""):
        raise StartRefused("El puerto local 8403 ya lo está usando el cluster cap2")

    monkeypatch.setattr(workflow.actions, "start_port", refuse)

    response = client.post(
        "/api/workflow/ports/start", json={"port": 8403}, params={"env": "dev", "cluster": "cap"}
    )

    assert response.status_code == 409
    assert "cap2" in response.json()["detail"]