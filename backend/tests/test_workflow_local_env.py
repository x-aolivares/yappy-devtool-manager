"""Un ambiente local no ofrece filas de AWS en el Workflow.

El caso que lo motivó: `env.local` declaraba `AWS_ENVIRONMENT=local` (lo que
documenta el template) y no `DB_MODE=local` (lo que leía `Config.is_local`). El
ambiente se trataba como remoto, la pantalla dibujaba la fila *Tunnel Aurora* y
el click terminaba en un 500 con `Missing required config: AWS_HOST`.

Estas pruebas fijan las dos mitades: el registro no ofrece lo que no puede
levantar, y el endpoint rechaza el intento aunque llegue por fuera de la
pantalla —un curl o una pestaña vieja— en vez de romper el worker thread.
"""

from __future__ import annotations

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.adapters.database import credentials
from yappy_library.application.workflow import actions
from yappy_library.application.workflow.actions import StartRefused
from yappy_library.application.workflow.registry import resources_for
from yappy_library.application.workflow.service import snapshot


class FakeConfig:
    """La porción de `Config` que leen el registro y el snapshot."""

    def __init__(self, is_local: bool = True, **values):
        self.env = "local" if is_local else "dev"
        self.is_local = is_local
        self._values = {
            "CLUSTER_DOMAIN": "yappycloud.com",
            "CLUSTER_PORTS": "cap=8403:commerce,8402:personal",
            "AWS_PORT": "53360",
            "DB_PORT": "8100",
            "KAFKA_PATH": "C:/kafka",
            **values,
        }

    def get(self, key, default=None):
        return self._values.get(key, default)

    def require(self, key):
        value = self._values.get(key)
        if not value:
            raise ValueError(f"Missing required config: {key}")
        return value

    @property
    def profile(self) -> str:
        return "base-profile"

    @property
    def db_port(self) -> int:
        return int(self._values.get("DB_PORT", "8100"))

    @property
    def kafka_core_path(self) -> str:
        return f"{self._values.get('KAFKA_PATH', 'C:/kafka')}/kafka-core"


@pytest.fixture(autouse=True)
def _isolated_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / "tracker")
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})


# --- el registro -------------------------------------------------------------


def test_a_local_env_only_gets_the_kafka_rows():
    keys = {definition.key for definition in resources_for(FakeConfig())}

    assert keys == {"kafka-server", "kafka-ui"}


def test_a_remote_env_still_gets_every_row():
    keys = {definition.key for definition in resources_for(FakeConfig(is_local=False))}

    assert {"kafka-server", "kafka-ui", "kafdrop-remote", "databricks", "bastion", "db"} <= keys


# --- el snapshot -------------------------------------------------------------


def test_the_snapshot_reports_a_local_env_and_hides_the_catalog():
    snap = snapshot(FakeConfig(), "cap")

    assert snap.is_local is True
    assert {row.key for row in snap.resources} == {"kafka-server", "kafka-ui"}
    # `CLUSTER_PORTS` se hereda de env.base, pero no hay AWS contra qué abrirlo.
    assert snap.ports == []


def test_the_snapshot_keeps_the_catalog_for_a_remote_env():
    snap = snapshot(FakeConfig(is_local=False), "cap")

    assert snap.is_local is False
    assert [row.port for row in snap.ports] == [8403, 8402]


def test_the_state_body_carries_is_local():
    from yappy_api.routes.workflow import _state_body

    body = _state_body(snapshot(FakeConfig(), "cap"))

    assert body["is_local"] is True
    assert {row["key"] for row in body["resources"]} == {"kafka-server", "kafka-ui"}


# --- las acciones ------------------------------------------------------------


def test_starting_the_aurora_row_on_a_local_env_never_touches_aws(monkeypatch):
    """La refusal sale del registro, antes de pedir un token de RDS."""

    def forbidden(cfg):
        raise AssertionError("se intentó generar un token de RDS en un ambiente local")

    monkeypatch.setattr(credentials, "generate_token", forbidden)

    with pytest.raises(StartRefused) as excinfo:
        actions.start_resource(FakeConfig(), "db")

    assert "local" in excinfo.value.reason


def test_starting_a_cluster_port_on_a_local_env_is_refused():
    with pytest.raises(StartRefused) as excinfo:
        actions.start_port(FakeConfig(), "cap", 8403)

    assert "local" in excinfo.value.reason


def test_starting_a_cluster_port_without_an_environment_is_refused():
    """Sin ambiente no hay a qué apuntar.

    El ámbito vacío es el de la pantalla de Kafka: full local, sin cluster y sin
    AWS. Un túnel se abre contra un ambiente, así que sin ambiente la respuesta
    es una refusal con el motivo —y no un ``ssm start-session`` apuntando a un
    host que no existe.
    """
    cfg = FakeConfig(is_local=False)
    cfg.env = ""

    with pytest.raises(StartRefused) as excinfo:
        actions.start_port(cfg, "cap", 8403)

    assert "ambiente" in excinfo.value.reason


def test_starting_a_resource_without_an_environment_only_knows_kafka():
    """El registro sin ambiente ofrece Kafka y nada más.

    Es la misma regla que en un ambiente local —no hay AWS— y por eso la
    pantalla de Kafka nunca ofrece un túnel que no puede abrir.
    """
    cfg = FakeConfig(is_local=False)
    cfg.env = ""

    assert {definition.key for definition in resources_for(cfg)} == {
        "kafka-server",
        "kafka-ui",
    }

    with pytest.raises(StartRefused) as excinfo:
        actions.start_resource(cfg, "bastion")

    assert "bastion" in excinfo.value.reason


def test_a_missing_aws_host_is_a_refusal_not_a_500(monkeypatch):
    """La otra mitad del bug: `cfg.require` lanzaba ValueError y escapaba."""

    def explode(cfg):
        raise ValueError("Missing required config: AWS_HOST")

    monkeypatch.setattr(credentials, "generate_token", explode)

    with pytest.raises(StartRefused) as excinfo:
        actions._start_db(FakeConfig(is_local=False), 8100)

    assert "AWS_HOST" in excinfo.value.reason
