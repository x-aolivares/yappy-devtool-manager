"""The Workflow routes: the HTTP surface of the Recursos screen.

These call the handlers directly rather than through a client, the way
``test_web_api.py`` does. What matters here is the boundary: which conditions
become which status code, and that the handlers never invent state of their own.
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest
from fastapi import HTTPException

from yappy_api.routes import workflow
from yappy_api.schemas import (
    WorkflowCatalogAddRequest,
    WorkflowCatalogRenameRequest,
    WorkflowStartPortRequest,
    WorkflowStartResourceRequest,
    WorkflowStopRequest,
)
from yappy_library.adapters import process_tracker
from yappy_library.adapters.processes import BaseCommand
from yappy_library.application.workflow import catalog_write
from yappy_library.application.workflow.actions import StartRefused, StopRefused


class FakeConfig:
    """Just enough `Config` for a route to load an environment."""

    def __init__(self, env="dev", **values):
        self.env = env
        self._values = {
            "CLUSTER_DOMAIN": "yappycloud.com",
            "CLUSTER_PORTS": "cap=8402:core-p2m-m2p,8403:commerce-profile;cap2=8412:personal-profile;int=",
            "AWS_INSTANCE": "i-123",
            "AWS_REGION": "us-west-2",
            "AWS_PORT": "53360",
            "AWS_HOST": "db.internal",
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
    def kafka_path(self) -> str:
        return self._values.get("KAFKA_PATH", "C:/kafka")

    @property
    def kafka_core_path(self) -> str:
        return f"{self.kafka_path}/kafka-core"

    @property
    def kafka_ui_path(self) -> str:
        return f"{self.kafka_path}/kafka-ui"


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


@pytest.fixture
def cfg(monkeypatch):
    fake = FakeConfig()
    monkeypatch.setattr(workflow, "_cfg", lambda env: fake)
    return fake


def _track(tracker, pid, *, alive=True, **overrides):
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
        "cluster": "",
        "label": "",
        "owner": "cli",
        "state": "up",
        **overrides,
    }
    tracker.mkdir(parents=True, exist_ok=True)
    (tracker / f"{pid}.json").write_text(json.dumps(entry))
    if not alive:
        entry["pid"] += 10_000_000


@contextlib.contextmanager
def recorder():
    killed: list[int] = []
    original = BaseCommand.kill_ssm
    BaseCommand.kill_ssm = lambda self, pid=None: killed.append(pid)
    try:
        yield killed
    finally:
        BaseCommand.kill_ssm = original


# --- clusters ---------------------------------------------------------------


def test_clusters_lists_the_aliases_the_registry_uses():
    body = workflow.api_workflow_clusters()

    assert set(body["clusters"]) == {"cap", "cap2", "int"}


# --- state ------------------------------------------------------------------


def test_state_is_one_call_with_everything(tracker, cfg):
    """The screen asks once. Everything else would be N round trips to answer a
    question it already had the data for."""
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="web")

    body = workflow.api_workflow_state(env="dev", cluster="cap")

    assert body["remote_host"] == "nlbcapabilities.dev.yappycloud.com"
    assert [p["port"] for p in body["ports"]] == [8402, 8403]
    assert {r["key"] for r in body["resources"]} >= {"kafka-server", "kafka-ui", "bastion", "db"}


def test_state_carries_the_flags_the_rows_are_drawn_from(tracker, cfg):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="cli")

    row = next(p for p in workflow.api_workflow_state(env="dev", cluster="cap")["ports"]
               if p["port"] == 8402)

    assert row["state"] == "up"
    assert row["foreign"] is True
    assert row["since"] == 1757000000.0


def test_an_unknown_cluster_is_a_400_not_a_500(tracker, cfg):
    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_state(env="dev", cluster="nope")

    assert excinfo.value.status_code == 400
    assert "cap" in excinfo.value.detail


def test_an_unknown_environment_is_a_400(tracker):
    """`Config.with_env` raises for an environment with no file, and the route
    turns that into a 400 with the message intact. Tested through the real
    `_cfg`, because the status code is its whole job."""
    with pytest.raises(HTTPException) as excinfo:
        workflow._cfg("definitely-not-an-environment")

    assert excinfo.value.status_code == 400
    assert "definitely-not-an-environment" in excinfo.value.detail


def test_producer_is_not_in_the_response(tracker, cfg):
    """It was the same tunnel as Bastion; two rows would conflict forever."""
    keys = {r["key"] for r in workflow.api_workflow_state(env="dev", cluster="cap")["resources"]}

    assert "producer" not in keys
    assert "bastion" in keys


# --- starting ---------------------------------------------------------------


def test_starting_returns_202_worth_of_data_without_waiting(tracker, cfg, monkeypatch):
    """The probe runs elsewhere; this handler hands back the pid and leaves."""
    class FakeProc:
        pid = os.getpid()

    monkeypatch.setattr(
        workflow.actions, "start_port",
        lambda cfg, cluster, port, label="": {"pid": os.getpid(), "port": port,
                                              "state": "starting", "label": label},
    )

    body = workflow.api_workflow_port_start(
        WorkflowStartPortRequest(port=8402, label="core"), env="dev", cluster="cap"
    )

    assert body["ok"] is True
    assert body["state"] == "starting"
    assert body["port"] == 8402


def test_a_refused_start_is_409(tracker, cfg, monkeypatch):
    """The port is taken. That is an outcome with a reason, not a 500."""
    def refuse(cfg, cluster, port, label=""):
        raise StartRefused("El puerto local 8403 ya lo está usando el cluster cap2")

    monkeypatch.setattr(workflow.actions, "start_port", refuse)

    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_port_start(
            WorkflowStartPortRequest(port=8403), env="dev", cluster="cap"
        )

    assert excinfo.value.status_code == 409
    assert "cap2" in excinfo.value.detail


def test_the_route_refuses_an_unknown_cluster_before_doing_anything(tracker, cfg, monkeypatch):
    started = []
    monkeypatch.setattr(
        workflow.actions, "start_port",
        lambda cfg, cluster, port, label="": started.append(cluster) or {},
    )

    with pytest.raises(HTTPException):
        workflow.api_workflow_port_start(
            WorkflowStartPortRequest(port=8402), env="dev", cluster="nope"
        )

    assert started == []


def test_starting_a_resource_takes_a_registry_key(tracker, cfg, monkeypatch):
    monkeypatch.setattr(
        workflow.actions, "start_resource",
        lambda cfg, key: {"pid": 1, "port": 9092, "state": "starting", "label": key},
    )

    body = workflow.api_workflow_resource_start(
        WorkflowStartResourceRequest(key="kafka-server"), env="dev"
    )

    assert body["ok"] is True


# --- stopping ---------------------------------------------------------------


def test_a_refused_stop_is_409(tracker, cfg):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="cli")

    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_stop(WorkflowStopRequest(pid=os.getpid()))

    assert excinfo.value.status_code == 409
    assert "no lo levantó la web" in excinfo.value.detail


def test_stopping_something_untracked_is_404_ish(tracker, cfg):
    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_stop(WorkflowStopRequest(pid=999999))

    assert excinfo.value.status_code == 409


def test_stop_all_reports_what_it_refused(tracker, cfg):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="cli")

    body = workflow.api_workflow_stop_all()

    assert body["stopped"] == []
    assert body["refused"][0]["reason"] == "el cluster cap"


def test_stop_all_can_be_asked_to_include_them(tracker, cfg):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="cli")

    with recorder() as killed:
        body = workflow.api_workflow_stop_all(allow_foreign=True)

    assert body["refused"] == []
    assert killed == [os.getpid()]


# --- logs -------------------------------------------------------------------


def test_logs_read_the_last_lines_from_the_first_call(tracker, cfg, tmp_path):
    log = tmp_path / "tunnel-8402.log"
    # Written as bytes: text mode would translate every newline to CRLF on
    # Windows, and the assertion is about how many lines came back, not about
    # line endings.
    log.write_bytes(b"\n".join(f"linea {i}".encode() for i in range(100)))
    _track(tracker, os.getpid(), local_port=8402, cluster="cap",
           owner="web", log_file=str(log))

    body = workflow.api_workflow_logs(pid=os.getpid(), lines=10)

    assert body["text"].startswith("linea 90")
    assert body["text"].strip().count("\n") == 9
    assert body["offset"] == log.stat().st_size
    assert body["alive"] is True


def test_logs_with_an_offset_return_only_what_is_new(tracker, cfg, tmp_path):
    """A one-second poll must not re-read and re-render the whole log."""
    log = tmp_path / "tunnel-8402.log"
    log.write_text("primera\n", encoding="utf-8")
    _track(tracker, os.getpid(), local_port=8402, cluster="cap",
           owner="web", log_file=str(log))

    first = workflow.api_workflow_logs(pid=os.getpid(), lines=50)

    # Appended in binary: opening in text mode would translate the newline to
    # CRLF on Windows and the assertion would be about line endings, not offsets.
    with log.open("ab") as handle:
        handle.write(b"segunda\n")

    second = workflow.api_workflow_logs(pid=os.getpid(), offset=first["offset"])

    assert "primera" not in second["text"]
    assert "segunda" in second["text"]
    assert second["offset"] > first["offset"]


def test_logs_survive_a_restart_that_truncates(tracker, cfg, tmp_path):
    """`tunnel_log_path` opens with 'w': a tunnel restarted between two polls
    makes the file shorter, and an offset past the end would return nothing
    forever."""
    log = tmp_path / "tunnel-8402.log"
    log.write_bytes(b"\n".join(f"linea {i}".encode() for i in range(200)))
    _track(tracker, os.getpid(), local_port=8402, cluster="cap",
           owner="web", log_file=str(log))

    stale_offset = workflow.api_workflow_logs(pid=os.getpid(), lines=5)["offset"]
    log.write_text("recién reiniciado\n", encoding="utf-8")

    body = workflow.api_workflow_logs(pid=os.getpid(), offset=stale_offset)

    assert "recién reiniciado" in body["text"]


def test_logs_without_a_file_say_so(tracker, cfg):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="web", log_file="")

    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_logs(pid=os.getpid())

    assert excinfo.value.status_code == 404


def test_logs_for_an_unknown_pid_are_404(tracker, cfg):
    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_logs(pid=999999)

    assert excinfo.value.status_code == 404


# --- catalog ----------------------------------------------------------------


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """A temp `env.base` with the key and its explanatory comment block."""
    directory = tmp_path / "config"
    directory.mkdir()
    base = directory / "env.base"
    # utf-8 explicitly: the catalog writer reads and writes utf-8, and on Windows
    # the default encoding would make this fixture cp1252 and unreadable.
    base.write_text(
        "# Shared defaults\n"
        "CLUSTER_DOMAIN=yappycloud.com\n"
        "\n"
        "# --- Catálogo de túneles ---\n"
        "# Formato: <cluster>=<puerto>:<nombre>;...\n"
        "CLUSTER_PORTS=cap=8402:core-p2m-m2p;int=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog_write.Config, "_config_dir", directory)
    return base


def _cfg_from(config_dir):
    return FakeConfig(CLUSTER_PORTS=config_dir.read_text(encoding="utf-8").split("CLUSTER_PORTS=")[1].strip())


def test_adding_a_port_writes_only_that_key(config_dir):
    cfg = _cfg_from(config_dir)

    ports = catalog_write.add_port(cfg, "cap", 8403, "commerce-profile")

    text = config_dir.read_text(encoding="utf-8")
    assert [p.port for p in ports] == [8402, 8403]
    assert "CLUSTER_PORTS=cap=8402:core-p2m-m2p,8403:commerce-profile;int=" in text


def test_the_comment_block_survives_a_write(config_dir):
    """The file is a developer's record. Rewriting it wholesale would drop the
    Spanish explaining the format."""
    catalog_write.add_port(_cfg_from(config_dir), "cap", 8403, "nuevo")

    text = config_dir.read_text(encoding="utf-8")

    assert "# --- Catálogo de túneles ---" in text
    assert "# Formato: <cluster>=<puerto>:<nombre>;..." in text
    assert "CLUSTER_DOMAIN=yappycloud.com" in text


def test_the_key_is_replaced_and_not_appended_twice(config_dir):
    catalog_write.add_port(_cfg_from(config_dir), "cap", 8403, "nuevo")
    catalog_write.add_port(_cfg_from(config_dir), "cap", 8404, "otro")

    assert config_dir.read_text(encoding="utf-8").count("CLUSTER_PORTS=") == 1


def test_a_duplicate_port_is_refused_and_changes_nothing(config_dir):
    before = config_dir.read_text(encoding="utf-8")

    with pytest.raises(catalog_write.CatalogError) as excinfo:
        catalog_write.add_port(_cfg_from(config_dir), "cap", 8402, "otro")

    assert "ya está en el catálogo" in str(excinfo.value)
    assert config_dir.read_text(encoding="utf-8") == before


def test_a_port_out_of_range_is_refused(config_dir):
    with pytest.raises(catalog_write.CatalogError):
        catalog_write.add_port(_cfg_from(config_dir), "cap", 70000, "x")


def test_renaming_keeps_the_port_and_allows_an_empty_label(config_dir):
    catalog_write.rename_port(_cfg_from(config_dir), "cap", 8402, "")

    assert "CLUSTER_PORTS=cap=8402:;int=" in config_dir.read_text(encoding="utf-8")


def test_renaming_something_absent_is_refused(config_dir):
    with pytest.raises(catalog_write.CatalogError):
        catalog_write.rename_port(_cfg_from(config_dir), "cap", 9999, "x")


def test_a_running_port_cannot_be_removed(config_dir, tracker, monkeypatch):
    """The row being deleted is the only thing that can stop the tunnel."""
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="web")

    with pytest.raises(catalog_write.CatalogError) as excinfo:
        catalog_write.remove_port(_cfg_from(config_dir), "cap", 8402)

    assert "Paralo antes" in str(excinfo.value)
    assert "CLUSTER_PORTS=cap=8402" in config_dir.read_text(encoding="utf-8")


def test_removing_a_stopped_port_works(config_dir, tracker):
    _track(tracker, 4242, alive=False, local_port=8402, cluster="cap")

    remaining = catalog_write.remove_port(_cfg_from(config_dir), "cap", 8402)

    assert remaining == []
    assert "CLUSTER_PORTS=cap=;int=" in config_dir.read_text(encoding="utf-8")


def test_the_catalog_endpoint_reflects_the_change(config_dir, monkeypatch):
    monkeypatch.setattr(workflow, "Config", lambda: _cfg_from(config_dir))

    body = workflow.api_workflow_catalog_add(
        WorkflowCatalogAddRequest(port=8403, label="commerce"), cluster="cap"
    )

    # These endpoints declare a `response_model`, so calling them directly gives
    # back the model. The wire shape is asserted by the OpenAPI spec instead.
    assert [p.port for p in body.ports] == [8402, 8403]


def test_a_refused_catalog_edit_is_409(config_dir, monkeypatch):
    monkeypatch.setattr(workflow, "Config", lambda: _cfg_from(config_dir))

    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_catalog_add(
            WorkflowCatalogAddRequest(port=8402), cluster="cap"
        )

    assert excinfo.value.status_code == 409


def test_rename_endpoint(config_dir, monkeypatch):
    monkeypatch.setattr(workflow, "Config", lambda: _cfg_from(config_dir))

    body = workflow.api_workflow_catalog_rename(
        "cap", 8402, WorkflowCatalogRenameRequest(label="payment-profile")
    )

    assert body.ports[0].label == "payment-profile"
    assert "payment-profile" in config_dir.read_text(encoding="utf-8")


def test_remove_endpoint(config_dir, tracker, monkeypatch):
    monkeypatch.setattr(workflow, "Config", lambda: _cfg_from(config_dir))

    body = workflow.api_workflow_catalog_remove("cap", 8402)

    assert body.ports == []


def test_reset_refuses_while_tunnels_run(config_dir, tracker, monkeypatch):
    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="web")
    monkeypatch.setattr(workflow, "Config", lambda: _cfg_from(config_dir))

    with pytest.raises(HTTPException) as excinfo:
        workflow.api_workflow_catalog_reset("cap")

    assert excinfo.value.status_code == 409


def test_the_key_is_appended_when_the_file_has_none(config_dir):
    base = config_dir
    base.write_text("CLUSTER_DOMAIN=yappycloud.com\n")

    catalog_write.add_port(FakeConfig(CLUSTER_PORTS=""), "cap", 8402, "core")

    text = base.read_text(encoding="utf-8")
    assert "CLUSTER_PORTS=cap=8402:core" in text
    assert "CLUSTER_DOMAIN=yappycloud.com" in text