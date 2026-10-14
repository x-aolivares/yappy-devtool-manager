"""The `CLUSTER_PORTS` catalog and the snapshot built from it."""

from __future__ import annotations

import json
import os

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.application.workflow import catalog as catalog_mod
from yappy_library.application.workflow.catalog import (
    catalog_for,
    entries_for,
    format_catalog,
    parse_catalog,
)
from yappy_library.application.workflow.models import (
    STATE_DOWN,
    STATE_DEAD,
    STATE_ERROR,
    STATE_UP,
    PortEntry,
    is_foreign,
    state_of,
)
from yappy_library.application.workflow.service import remote_host_for, snapshot


class FakeConfig:
    """The slice of `Config` the workflow package reads."""

    def __init__(self, env="dev", **values):
        self.env = env
        self._values = {
            "CLUSTER_DOMAIN": "yappycloud.com",
            "KAFKA_PATH": "C:/kafka",
            **values,
        }

    def get(self, key, default=None):
        return self._values.get(key, default)

    @property
    def db_port(self) -> int:
        return int(self._values.get("DB_PORT", "8100"))

    @property
    def kafka_core_path(self) -> str:
        return f"{self._values.get('KAFKA_PATH', 'C:/kafka')}/kafka-core"


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
        "cluster": "",
        "label": "",
        "owner": "cli",
        "state": "up",
        **overrides,
    }
    tracker.mkdir(parents=True, exist_ok=True)
    (tracker / f"{pid}.json").write_text(json.dumps(entry))


# --- catalog ----------------------------------------------------------------


def test_parses_ports_and_labels():
    parsed = parse_catalog("cap=8402:core-p2m-m2p,8403:commerce-profile;int=")

    assert parsed["cap"] == [
        PortEntry(port=8402, label="core-p2m-m2p"),
        PortEntry(port=8403, label="commerce-profile"),
    ]
    assert parsed["int"] == []


def test_a_port_can_be_forwarded_before_it_is_named():
    assert parse_catalog("cap2=8413:")[ "cap2"] == [PortEntry(port=8413, label="")]


def test_a_bad_entry_does_not_cost_the_group():
    """One typo should not empty the developer's whole cluster."""
    parsed = parse_catalog("cap=8402:core,notaport:x,8403:commerce")

    assert [e.port for e in parsed["cap"]] == [8402, 8403]


def test_a_repeated_port_is_collapsed():
    # One port, one tunnel. Two rows would claim to be able to stop two things.
    parsed = parse_catalog("cap=8402:core,8402:otro")

    assert parsed["cap"] == [PortEntry(port=8402, label="core")]


def test_every_known_cluster_is_present():
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core")

    parsed = catalog_for(cfg)

    assert set(parsed) >= {"cap", "cap2", "int"}
    assert parsed["cap2"] == []


def test_catalog_is_keyed_by_cluster_not_by_environment():
    ports = "cap=8402:core"
    assert entries_for(FakeConfig("dev", CLUSTER_PORTS=ports), "cap") == entries_for(
        FakeConfig("uat", CLUSTER_PORTS=ports), "cap"
    )


def test_format_round_trips():
    raw = "cap=8402:core-p2m-m2p,8403:;cap2=8412:personal-profile;int="

    assert catalog_mod.parse_catalog(format_catalog(parse_catalog(raw))) == parse_catalog(raw)


# --- state derivation -------------------------------------------------------


def test_liveness_beats_the_recorded_state():
    assert state_of({"alive": False, "state": "up"}) == STATE_DEAD
    assert state_of({"alive": True, "state": "up"}) == STATE_UP
    assert state_of({"alive": True, "state": "error"}) == STATE_ERROR
    assert state_of({"alive": True}) == STATE_UP


def test_only_the_web_counts_as_its_own():
    assert is_foreign({"owner": "cli"}) is True
    assert is_foreign({"owner": "web"}) is False


# --- snapshot ---------------------------------------------------------------


def test_snapshot_reads_a_tunnel_the_cli_started(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="cli")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core-p2m-m2p")

    row = snapshot(cfg, "cap").ports[0]

    assert row.state == STATE_UP
    # The screen's whole promise: a tunnel opened from the terminal shows up,
    # and it is marked as not the web's.
    assert row.foreign is True
    assert row.pid == pid
    assert row.label == "core-p2m-m2p"


def test_a_catalog_row_with_nothing_running_is_down(tracker):
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core")

    row = snapshot(cfg, "cap").ports[0]

    assert row.state == STATE_DOWN
    assert row.pid is None
    assert row.browser_url is None


def test_a_dead_tunnel_is_dead_not_down(tracker):
    _track(tracker, 2147480000, local_port=8402, cluster="cap")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core")

    assert snapshot(cfg, "cap").ports[0].state == STATE_DEAD


def test_a_port_held_by_another_cluster_conflicts(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8403, cluster="cap2", owner="web")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core,8403:commerce")

    rows = {r.port: r for r in snapshot(cfg, "cap").ports}

    # cap2 holds the port, so cap's row is *not* up: it is the row that cannot
    # start until the port is free. Adopting cap2's tunnel here would claim a
    # tunnel this screen cannot stop.
    assert rows[8403].state == STATE_DOWN
    assert rows[8403].conflict == "el cluster cap2"


def test_the_holding_cluster_sees_its_own_tunnel(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8403, cluster="cap2", owner="web")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core;cap2=8403:personal-profile")

    row = snapshot(cfg, "cap2").ports[0]

    assert row.state == STATE_UP
    assert row.conflict is None


def test_a_dead_holder_does_not_conflict(tracker):
    """The port is free again; nothing is in the way."""
    _track(tracker, 2147480000, local_port=8403, cluster="cap2")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8403:commerce")

    assert snapshot(cfg, "cap").ports[0].conflict is None


def test_a_tunnel_from_before_the_port_field_is_set_apart(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=None, cluster="cap")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core")

    snap = snapshot(cfg, "cap")

    assert [p.port for p in snap.ports] == [8402]
    assert [e["pid"] for e in snap.unattributed] == [pid]


def test_resources_carry_their_own_state(tracker):
    pid = os.getpid()
    _track(tracker, pid, resource="kafka", target="ui", local_port=8080, owner="web")
    cfg = FakeConfig(CLUSTER_PORTS="cap=")

    rows = {r.key: r for r in snapshot(cfg, "cap").resources}

    assert rows["kafka-ui"].state == STATE_UP
    assert rows["kafka-ui"].foreign is False
    assert rows["kafka-server"].state == STATE_DOWN
    assert rows["db"].local_port == 8100


def test_snapshot_carries_the_resolved_host():
    cfg = FakeConfig("qa", CLUSTER_PORTS="cap=")

    assert remote_host_for(cfg, "cap") == "nlbcapabilities.qa.yappycloud.com"
    assert remote_host_for(cfg, "cap2") == "nlbcapabilities.v2.qa.yappycloud.com"
    assert remote_host_for(cfg, "nope") == ""


def test_snapshot_is_one_read_of_the_registry(tracker, monkeypatch):
    """One poll, one pass. A snapshot that re-read per row would be N times the
    filesystem work for the same answer."""
    _track(tracker, os.getpid(), local_port=8402, cluster="cap")
    cfg = FakeConfig(CLUSTER_PORTS="cap=8402:core,8403:commerce")

    calls = []
    real = process_tracker.get_tracked_processes

    def counting(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("yappy_library.application.workflow.service.get_tracked_processes", counting)
    snapshot(cfg, "cap")

    assert len(calls) == 1