"""The tracker entry format, and what a reader does with an old one.

These are the guarantees the web UI's resource screen is built on: a tracked
tunnel says which local port it forwards, entries written by an older version
still read, and the RDS token refresher is not mistaken for a tunnel.
"""

from __future__ import annotations

import json
import os

import pytest

from yappy_library.adapters import process_tracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """Point the tracker at a temp dir with an empty parse cache."""
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


def _write(directory, pid: int, entry: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(json.dumps(entry))


#: The shape written before `local_port` existed. Files like this outlive the
#: code that wrote them, so the reader has to cope with one.
def _legacy_entry(pid: int) -> dict:
    return {
        "pid": pid,
        "resource": "tunnel",
        "target": "base-profile",
        "env": "",
        "started_at": 1757000000.0,
        "log_file": "",
    }


def test_tracked_tunnel_records_the_port_it_forwards(tracker):
    process_tracker.track_process(
        pid=os.getpid(),
        resource="tunnel",
        target="base-profile",
        env="dev",
        local_port=8402,
        remote_port=8402,
        remote_host="nlbcapabilities.dev.yappycloud.com",
        cluster="cap",
        label="core-p2m-m2p",
    )

    entry = process_tracker.get_tracked_processes()[0]

    assert entry["local_port"] == 8402
    assert entry["remote_port"] == 8402
    assert entry["remote_host"] == "nlbcapabilities.dev.yappycloud.com"
    assert entry["cluster"] == "cap"
    assert entry["label"] == "core-p2m-m2p"
    assert entry["env"] == "dev"
    assert entry["alive"] is True


def test_liveness_is_read_live_not_stored(tracker):
    """A stored `alive` would hide the one event this registry exists for."""
    _write(tracker, os.getpid(), {**_legacy_entry(os.getpid()), "alive": True})
    # The OS disagrees with the file, and the OS wins.
    _write(tracker, os.getpid(), {**_legacy_entry(os.getpid()), "alive": True})
    entry = process_tracker.get_tracked_processes()[0]
    assert entry["alive"] is True

    # A pid that cannot exist reads as dead even though the file says otherwise.
    _write(tracker, 2147480000, {**_legacy_entry(2147480000), "alive": True})
    dead = [e for e in process_tracker.get_tracked_processes() if e["pid"] == 2147480000]
    assert dead[0]["alive"] is False


def test_old_entry_reads_as_the_clis_and_keeps_no_port(tracker):
    pid = os.getpid()
    _write(tracker, pid, _legacy_entry(pid))

    entry = process_tracker.get_tracked_processes()[0]

    assert entry["owner"] == process_tracker.OWNER_CLI
    assert entry["state"] == process_tracker.STATE_UP
    # Not 0: "written before the field existed" and "forwards port 0" are
    # different, and a caller drawing one row per port has to tell them apart.
    assert entry["local_port"] is None


def test_refresher_is_not_a_tunnel(tracker):
    """`kill_ssm()` selects on `resource="tunnel"` and was killing this one."""
    process_tracker.track_process(
        pid=os.getpid(), resource="refresher", target="dev", log_file="/tmp/x.log"
    )
    assert process_tracker.get_tracked_processes(resource="tunnel") == []

    process_tracker.track_process(
        pid=os.getpid() + 1, resource="tunnel", target="dev", local_port=8100
    )
    tunnels = process_tracker.get_tracked_processes(resource="tunnel")
    assert [t["resource"] for t in tunnels] == ["tunnel"]


def test_update_state_moves_only_the_state(tracker):
    process_tracker.track_process(
        pid=os.getpid(), resource="tunnel", target="dev", local_port=8402
    )

    process_tracker.update_process_state(os.getpid(), process_tracker.STATE_ERROR)

    entry = process_tracker.get_tracked_processes()[0]
    assert entry["state"] == process_tracker.STATE_ERROR
    assert entry["local_port"] == 8402


def test_rewriting_a_file_is_picked_up(tracker):
    """The web reads entries a CLI wrote; the cache must not pin the first one."""
    pid = os.getpid()
    _write(tracker, pid, _legacy_entry(pid))
    assert process_tracker.get_tracked_processes()[0]["label"] == ""

    _write(tracker, pid, {**_legacy_entry(pid), "label": "commerce-profile"})
    assert process_tracker.get_tracked_processes()[0]["label"] == "commerce-profile"


def test_returned_entries_are_copies(tracker):
    """Callers annotate what they get back; a shared dict would leak between them."""
    process_tracker.track_process(
        pid=os.getpid(), resource="tunnel", target="dev", local_port=8402
    )

    first = process_tracker.get_tracked_processes()[0]
    first["alive"] = False
    first["label"] = "mutated"

    second = process_tracker.get_tracked_processes()[0]
    assert second["alive"] is True
    assert second["label"] == ""


def test_untracked_process_disappears(tracker):
    process_tracker.track_process(pid=os.getpid(), resource="tunnel", target="dev")
    assert len(process_tracker.get_tracked_processes()) == 1

    process_tracker.untrack_process(os.getpid())

    assert process_tracker.get_tracked_processes() == []


def test_unreadable_file_is_skipped_not_fatal(tracker):
    tracker.mkdir(parents=True, exist_ok=True)
    (tracker / "999999.json").write_text("{not json")
    process_tracker.track_process(
        pid=os.getpid(), resource="tunnel", target="dev", local_port=8402
    )

    entries = process_tracker.get_tracked_processes()

    assert [e["local_port"] for e in entries] == [8402]


def test_el_orden_es_por_escritura_y_no_por_nombre(tracker):
    """"Newest first" ordenado por nombre de archivo es ordenar pids como texto:
    "9999.json" queda antes que "33940.json", que es al revés. Con dos entradas
    del mismo puerto, eso hace que cuál sea "la dueña" dependa de la cantidad de
    dígitos del pid."""
    import time as _time

    for pid in (9999, 33940):
        _write(tracker, pid, {**_legacy_entry(pid), "local_port": 8402})
        # mtime distintos y deterministas: el sistema de archivos puede tener
        # resolución gruesa y dos escrituras en el mismo tick se empardarían.
        _time.sleep(0.01)

    order = [e["pid"] for e in process_tracker.get_tracked_processes()]

    # 33940 se escribió después, así que va primero aunque su nombre sea mayor.
    assert order == [33940, 9999]


def test_prune_dead_on_port_solo_toca_ese_puerto(tracker):
    pid_vivo = os.getpid()
    _write(tracker, pid_vivo, {**_legacy_entry(pid_vivo), "local_port": 8403})
    _write(tracker, 4242, {**_legacy_entry(4242), "local_port": 8402})

    forgotten = process_tracker.prune_dead_on_port(8402)

    assert forgotten == [4242]
    assert [e["pid"] for e in process_tracker.get_tracked_processes()] == [pid_vivo]