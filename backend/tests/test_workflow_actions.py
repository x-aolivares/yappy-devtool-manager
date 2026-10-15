"""The start and stop rules, exercised without touching AWS or a real process.

Everything here runs against fake pids in a temp tracker: the point of these
tests is the decisions — who may be stopped, what a lost port race does, where a
start ends up recorded — not the spawning, which the adapter tests cover.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.adapters.processes import BaseCommand
from yappy_library.application.workflow import actions
from yappy_library.application.workflow import supervisor as supervisor_module
from yappy_library.application.workflow.actions import (
    StartRefused,
    StopRefused,
    ensure_port_free,
    start_port,
    stop_all,
    stop_entry,
)


class FakeConfig:
    """The slice of `Config` the actions read."""

    def __init__(self, env="dev", **values):
        self.env = env
        self._values = {
            "CLUSTER_DOMAIN": "yappycloud.com",
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
            raise ValueError(key)
        return value

    @property
    def profile(self) -> str:
        return "base-profile"

    @property
    def db_port(self) -> int:
        return int(self._values.get("DB_PORT", "8100"))


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


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
        # The OS is the authority on liveness; a fake pid is simply not there.
        entry["pid"] = entry["pid"] + 10_000_000


@contextlib.contextmanager
def recorder():
    """Capture the pids handed to `kill_ssm` instead of killing anything."""
    killed: list[int] = []
    original = BaseCommand.kill_ssm
    # `kill_ssm` answers whether a kill was attempted. `append` returns None, so
    # the `or True` is what keeps the seam faithful to the real contract: without
    # it every stop reads as "refused" and the wiring under test stops asserting
    # anything.
    BaseCommand.kill_ssm = lambda self, pid=None: killed.append(pid) or True
    try:
        yield killed
    finally:
        BaseCommand.kill_ssm = original


# --- the port race ----------------------------------------------------------


def test_another_clusters_live_tunnel_blocks_the_start(tracker):

    _track(tracker, os.getpid(), local_port=8403, cluster="cap2", owner="web")

    with pytest.raises(StartRefused) as excinfo:
        ensure_port_free(8403, cluster="cap")

    assert "cap2" in excinfo.value.reason


def test_this_rows_own_tunnel_does_not_block_it(tracker):

    _track(tracker, os.getpid(), local_port=8403, cluster="cap", owner="web")

    ensure_port_free(8403, cluster="cap")  # no raise: it is this row's own tunnel


def test_a_dead_holder_does_not_block_the_start(tracker):
    _track(tracker, 4242, alive=False, local_port=8403, cluster="cap2")

    ensure_port_free(8403, cluster="cap")


def test_a_start_limpia_los_fantasmas_del_puerto(tracker):
    """Un proceso que se cae deja su entrada atrás para siempre.

    `ensure_port_free` sólo mira las vivas, así que el puerto parece libre y cada
    intento apila otro fantasma encima. El registro crece sin límite y el
    conflicto de una fila termina parpadeando."""
    for pid in (100, 200, 300):
        _track(tracker, pid, alive=False, local_port=8402, cluster="cap")

    ensure_port_free(8402, cluster="cap")

    assert process_tracker.get_tracked_processes() == []


def test_limpiar_un_puerto_no_toca_los_otros(tracker):
    _track(tracker, 100, alive=False, local_port=8402, cluster="cap")
    _track(tracker, os.getpid(), local_port=8403, cluster="cap")

    ensure_port_free(8402, cluster="cap")

    assert [e["local_port"] for e in process_tracker.get_tracked_processes()] == [8403]


def test_limpiar_no_toca_procesos_vivos_de_otro_puerto(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap")

    ensure_port_free(8403, cluster="cap")

    assert [e["pid"] for e in process_tracker.get_tracked_processes()] == [pid]


def test_the_refusal_names_the_other_holder(tracker):
    """The screen shows this string; "already in use" without a subject would
    send the developer looking in the wrong place."""

    _track(tracker, os.getpid(), local_port=8403, cluster="cap2", owner="web")

    with pytest.raises(StartRefused) as excinfo:
        ensure_port_free(8403, cluster="cap")

    assert "8403" in excinfo.value.reason
    assert "cap2" in excinfo.value.reason


# --- starting ---------------------------------------------------------------


@pytest.fixture
def fake_spawn(monkeypatch):
    """Replace the SSM spawn with one that only writes the tracker entry.

    Returns the pid it handed out, so a test can read the tracker afterwards.
    """
    pid = os.getpid()

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

    def spawn(cfg, **kwargs):
        process_tracker.track_process(
            pid=pid, resource="tunnel", target="dev",
            local_port=kwargs["local_port"], cluster=kwargs["cluster"],
            owner="web",
        )
        return FakeProc(pid)

    monkeypatch.setattr(actions, "_spawn_tunnel", spawn)
    return pid


def test_a_start_is_recorded_as_starting_before_the_probe_runs(tracker, fake_spawn, monkeypatch):
    """A snapshot in the gap between spawn and probe must not read `down`:
    `down` invites a second click on a port that is already being opened."""
    seen = {}

    def fake_supervise(pid, port, probe, label="", timeout=None, log_file=""):
        seen["state_at_handoff"] = process_tracker.get_tracked_processes()[0]["state"]
        return None

    monkeypatch.setattr(actions, "supervise", fake_supervise)

    result = start_port(FakeConfig(CLUSTER_PORTS=""), "cap", 8402)

    assert seen["state_at_handoff"] == process_tracker.STATE_STARTING
    assert result["state"] == process_tracker.STATE_STARTING
    assert result["port"] == 8402
    assert result["pid"] == fake_spawn


def test_starting_uses_the_https_probe(tracker, fake_spawn, monkeypatch):
    """A cluster tunnel terminates TLS; probing it as plain HTTP would never
    answer and every row would end up in error."""
    seen = {}

    monkeypatch.setattr(
        actions, "supervise",
        lambda pid, port, probe, label="", timeout=None, log_file="": seen.update(probe=probe),
    )

    start_port(FakeConfig(CLUSTER_PORTS=""), "cap", 8402)

    assert seen["probe"] == "https"


def test_an_unknown_cluster_is_refused(tracker):
    with pytest.raises(StartRefused) as excinfo:
        start_port(FakeConfig(), "nope", 8402)

    assert "nope" in excinfo.value.reason


def test_a_start_that_lost_the_race_spawns_nothing(tracker, monkeypatch):

    _track(tracker, os.getpid(), local_port=8403, cluster="cap2", owner="web")
    spawned = []
    monkeypatch.setattr(
        actions, "_spawn_tunnel",
        lambda cfg, **kwargs: spawned.append(kwargs) or object(),
    )

    with pytest.raises(StartRefused):
        start_port(FakeConfig(), "cap", 8403)

    assert spawned == []


# --- stopping ---------------------------------------------------------------


def test_a_row_button_will_not_stop_someone_elses_process(tracker):
    """`kill_ssm()` does not care who started what, so this check is the only
    thing between a row button and another project's tunnel."""
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="cli")

    with recorder() as killed:
        with pytest.raises(StopRefused) as excinfo:
            stop_entry(pid)

    assert killed == []
    assert "no lo levantó la web" in excinfo.value.reason


def test_a_row_button_stops_the_web_s_own_process(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="web")

    with recorder() as killed:
        result = stop_entry(pid)

    assert result["stopped"] is True
    assert killed == [pid]


def test_stopping_something_untracked_is_refused(tracker):
    with pytest.raises(StopRefused):
        stop_entry(999999)


def test_stop_all_leaves_foreign_processes_alone(tracker):

    _track(tracker, os.getpid(), local_port=8402, cluster="cap", owner="cli")

    result = stop_all()

    assert result["stopped"] == []
    assert [r["pid"] for r in result["refused"]] == [os.getpid()]


def test_parar_todo_dice_que_paro(tracker, monkeypatch):
    """The happy path used to be the silent one.

    A "Parar todo" that worked left the terminal with a `POST …/stop-all 200 OK`
    from uvicorn and not one word about what it had just closed. Refusals were
    logged, successes were not — so the answer to "did that do anything?" was
    only ever visible on the screen, and only until the next poll moved it.
    """
    parado = os.getpid()
    _track(tracker, parado, local_port=8402, cluster="cap", owner="web")
    monkeypatch.setattr(actions, "stop_pid", lambda pid: True)

    lineas: list[str] = []
    monkeypatch.setattr(actions, "log_info", lineas.append)
    stop_all()

    fallos = [l for l in lineas if l.startswith(f"Se paró {parado} (")]
    assert fallos, lineas
    # It has to name the row, not just the number: a bare pid is the same thing
    # the developer already saw in the URL bar.
    assert "cap" in fallos[0], fallos[0]


def test_el_andamaje_del_supervisor_no_vuelve():
    """Four `[wf]` step lines were scaffolding for a start that was failing.

    They described plumbing that either worked or raised — pid, port, probe,
    supervisor pid, "the request comes back here" — six lines per start. The
    verdicts the supervisor writes afterwards are the ones worth reading and
    they stay; this pins only that the chatter does not grow back.
    """
    fuente = (
        Path(actions.__file__).read_text(encoding="utf-8")
        + Path(supervisor_module.__file__).read_text(encoding="utf-8")
    )
    for ruido in (
        "proceso pid=",
        "anotado 'starting'",
        "supervisor entregado",
        "arranca: pid=",
    ):
        assert ruido not in fuente, ruido


def test_the_two_refusals_do_not_print_the_same_sentence(tracker, monkeypatch):
    """A guard refusal and a foreign one are different problems.

    They used to share one sentence, so a pid the guard refused because it is an
    *ancestor of this server* printed "no lo levantó la web". That sends the
    reader hunting for an ownership problem that does not exist — the same class
    of lie as reporting the pid under `stopped`, one layer down. The real reason
    is already on stderr from `_kill_pid`; this line only has to stop claiming a
    different one.
    """
    ajeno = os.getpid()
    # Ambos tienen que existir de verdad: el tracker lee la liveness del SO y
    # saltea lo que no encuentra. `getppid()` sirve igual que `getpid()` para
    # esto — nadie lo va a matar, solo se lo apunta en el tracker.
    protegido = os.getppid()
    _track(tracker, ajeno, local_port=8402, cluster="cap", owner="cli")
    _track(tracker, protegido, local_port=8403, cluster="cap", owner="web")
    monkeypatch.setattr(actions, "stop_pid", lambda pid: pid != protegido)

    lineas: list[str] = []
    monkeypatch.setattr(actions, "log_info", lineas.append)
    # sin allow_foreign: el ajeno se rechaza por dueño y el del guard pasa el
    # filtro de dueño (es de la web) y se cae en el guard. Los dos caminos.
    stop_all()

    ajeno_txt = [l for l in lineas if l.startswith(f"No se paró {ajeno}:")]
    protegido_txt = [l for l in lineas if l.startswith(f"No se paró {protegido}:")]
    assert ajeno_txt and protegido_txt, lineas
    assert "no lo levantó la web" in ajeno_txt[0]
    # The pid the guard refused must NOT be blamed on ownership.
    assert "no lo levantó la web" not in protegido_txt[0], protegido_txt[0]
    assert "guard" in protegido_txt[0].lower(), protegido_txt[0]


def test_stop_all_reports_what_it_refused_and_why(tracker):
    """The screen's confirmation says how many are somebody else's; that number
    has to come from here, not be guessed in the browser."""

    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="cli")

    refused = stop_all()["refused"]

    assert refused[0]["reason"] == "el cluster cap"


def test_stop_all_can_be_told_to_include_them(tracker):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="cli")

    with recorder() as killed:
        result = stop_all(allow_foreign=True)

    assert result["refused"] == []
    assert killed == [pid]


def test_stop_all_reports_a_refused_kill_as_refused(tracker, monkeypatch):
    """A guard that said no must not be reported as a stop.

    ``stop_pid`` used to return ``True`` after asking ``kill_ssm``, whatever the
    answer was, so the screen answered "Paré 2 procesos" for a run where the
    guard had refused both — and the developer, seeing the tunnel still up,
    pressed the button again. The process is the only witness that matters.
    """
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="web")
    monkeypatch.setattr(BaseCommand, "kill_ssm", lambda self, pid=None: False)

    result = stop_all(allow_foreign=True)

    assert result["stopped"] == []
    assert [item["pid"] for item in result["refused"]] == [pid]


def test_stop_entry_tells_the_truth_when_the_guard_refuses(tracker, monkeypatch):
    pid = os.getpid()
    _track(tracker, pid, local_port=8402, cluster="cap", owner="web")
    monkeypatch.setattr(BaseCommand, "kill_ssm", lambda self, pid=None: False)

    assert stop_entry(pid)["stopped"] is False


def test_stop_all_ignores_processes_that_are_already_gone(tracker):
    _track(tracker, 4242, alive=False, local_port=8402, cluster="cap", owner="web")

    assert stop_all(allow_foreign=True) == {"stopped": [], "refused": []}