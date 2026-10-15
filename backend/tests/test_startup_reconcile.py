"""Reconciliation of the tracker at web startup.

``~/.yappy/tracker/<pid>.json`` outlives the process it names. This file pins
what happens to the entries a previous session left behind: the dead ones
disappear, the live ones nobody supervises any more get stopped, and **the ones
a developer started in their own terminal are not touched**.

That last one is the one that matters most and the one that is easiest to get
wrong: `yappy web` must not reach into a tunnel somebody opened in their own
shell, even when the process that was going to supervise it is long gone.

Nothing here touches a real process. Live pids are this pytest interpreter's
own, dead ones are pids that cannot exist, and `kill_ssm` is replaced by a
recorder. The policy is exercised separately, with pids nobody ever spawned.
"""

from __future__ import annotations

import contextlib
import io
import json
import os

import pytest

from yappy_api import reconcile
from yappy_api.reconcile import (
    KEEP,
    KILL,
    REASON_FOREIGN,
    REASON_GHOST,
    REASON_LIVE,
    REASON_ORPHAN,
    UNTRACK,
    plan,
)
from yappy_library.adapters import logging as log_mod
from yappy_library.adapters import process_tracker
from yappy_library.adapters.process_tracker import OWNER_CLI, OWNER_WEB
from yappy_library.adapters.processes import BaseCommand

#: A pid that cannot exist: Windows hands out small pids and this one is not in
#: the toolhelp snapshot. Same trick as `test_process_tracker_schema.py`.
DEAD_PID = 2147480000

#: The other pid this file needs: the previous web server, already gone.
GONE_PID = 2147480001


@pytest.fixture
def tracker():
    """The temp tracker `conftest.py` installed, handed back for readability."""
    return process_tracker._TRACKER_DIR


@pytest.fixture
def kills():
    """Capture the pids handed to `kill_ssm` instead of killing anything."""
    killed: list[int] = []
    original = BaseCommand.kill_ssm

    # `kill_ssm` answers whether a kill was attempted. `append` returns None, so
    # the `or True` keeps the seam faithful to the real contract: without it
    # every orphan reads as a refusal and the wiring under test stops asserting
    # anything.
    BaseCommand.kill_ssm = lambda self, pid=None: killed.append(pid) or True
    try:
        yield killed
    finally:
        BaseCommand.kill_ssm = original


def _track(directory, pid, **overrides) -> dict:
    entry = {
        "pid": pid,
        "resource": "tunnel",
        "target": "base-profile",
        "env": "dev",
        "started_at": 1757000000.0,
        "log_file": "",
        "local_port": 9092,
        "remote_port": 9092,
        "remote_host": "",
        "cluster": "cap",
        "label": "Kafka Server",
        "owner": OWNER_WEB,
        "state": "up",
        "supervisor": None,
        "exe": "",
        "create_time": 0.0,
        **overrides,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(json.dumps(entry))
    return entry


def _pids() -> list[int]:
    return [e["pid"] for e in process_tracker.get_tracked_processes()]


def _capture(call):
    """Run ``call`` with both rich consoles pointed at a buffer; return the text.

    `capsys` does not reach it: the rich `Console` kept a reference to the
    `sys.stderr` that existed at import time. Same trick as
    `tests/test_log_timestamps.py`.
    """
    buffer = io.StringIO()
    err_console, out_console = log_mod._err_console, log_mod._console
    before = (err_console._file, out_console._file)
    err_console._file = buffer
    out_console._file = buffer
    try:
        call()
    finally:
        err_console._file, out_console._file = before
    return buffer.getvalue()


def _capture_result(call):
    """``_capture`` for a call that has a return value: ``(printed, result)``."""
    captured = {}
    printed = _capture(lambda: captured.update(result=call()))
    return printed, captured["result"]


def _flat(text: str) -> str:
    """Captured output with the console's line wrapping undone.

    rich wraps at the terminal width, which is not the width the assertions were
    written at, so a summary like "1 fantasmas olvidados" arrives split across two
    lines. Collapsing whitespace makes the assertions about content instead of
    about column width.
    """
    return " ".join(text.split())


# --- 1. a dead web-owned entry disappears, and nothing is killed ---------------


def test_a_dead_web_entry_disappears_and_nothing_is_killed(tracker, kills):
    """A process that fell over has nothing left to stop, but it does leave the
    file behind."""
    _track(tracker, DEAD_PID, owner=OWNER_WEB, supervisor=os.getpid())

    result = reconcile.reconcile()

    assert kills == []
    assert _pids() == []
    assert result["ghosts"] == 1


def test_a_dead_entry_stops_answering_ensure_port_free(tracker, kills):
    """Why this runs at startup rather than on every snapshot: `ensure_port_free`
    looks at liveness alone, so a dead entry keeps looking like a taken port for
    as long as the file exists."""
    from yappy_library.application.workflow.actions import ensure_port_free

    _track(tracker, DEAD_PID, owner=OWNER_WEB, local_port=9092)

    reconcile.reconcile()

    ensure_port_free(9092, cluster="cap")  # does not raise: the ghost is gone


# --- 2. a live orphan of a dead web session is killed and disappears -----------


def test_a_live_orphan_of_a_dead_session_is_killed_and_disappears(tracker, kills):
    """Nobody asked for it, nobody will supervise it any more, and it is holding a
    port the session about to start needs. This is what the whole thing is for."""
    _track(tracker, os.getpid(), owner=OWNER_WEB, supervisor=GONE_PID)

    result = reconcile.reconcile()

    assert kills == [os.getpid()]
    assert _pids() == []
    assert result["killed"] == 1


def test_the_orphan_goes_through_the_three_gates_not_a_bare_taskkill(tracker):
    """It goes out through `kill_ssm` rather than `os.kill`, because `kill_ssm` is
    what runs the ancestor guard and the two identity checks before touching
    anything. Swapping the method out is how this asserts the seam."""
    seen = []
    original = BaseCommand.kill_ssm

    BaseCommand.kill_ssm = lambda self, pid=None: seen.append(pid) or True
    try:
        _track(tracker, os.getpid(), owner=OWNER_WEB, supervisor=GONE_PID)
        reconcile.reconcile()
    finally:
        BaseCommand.kill_ssm = original

    assert seen == [os.getpid()]


def test_a_refused_kill_forgets_the_entry_and_says_so(tracker):
    """A recycled pid, or an ancestor of this server, make `kill_ssm` answer
    False. The process stays alive — the guard did its job — but the entry names
    a pid this tool can no longer claim, and keeping it draws a stranger on
    screen wearing our identity fields."""
    original = BaseCommand.kill_ssm
    BaseCommand.kill_ssm = lambda self, pid=None: False
    try:
        _track(tracker, os.getpid(), owner=OWNER_WEB, supervisor=GONE_PID)
        printed, result = _capture_result(reconcile.reconcile)
    finally:
        BaseCommand.kill_ssm = original

    assert result["killed"] == 0
    assert result["refused"] == 1
    assert _pids() == []
    assert "guard se negó" in _flat(printed)


# --- 3. a live owner=cli entry is left running and stays in the tracker --------


def test_a_live_cli_entry_is_left_running_and_stays_tracked(tracker, kills):
    """The strongest not-our-process promise in the project: the web does not
    reach into the developer's terminal."""
    _track(tracker, os.getpid(), owner=OWNER_CLI)

    result = reconcile.reconcile()

    assert kills == []
    assert _pids() == [os.getpid()]
    assert result["foreign"] == 1


def test_the_web_does_not_kill_a_cli_process_whose_supervisor_died(tracker, kills):
    """Only the web writes the ``supervisor`` field (`actions._hand_off`). The day
    a terminal entry carries one, rule 1 still may not win: "my web server is
    gone" is not a reason to kill a process the web never started."""
    _track(tracker, os.getpid(), owner=OWNER_CLI, supervisor=GONE_PID)

    reconcile.reconcile()

    assert kills == []
    assert _pids() == [os.getpid()]


# --- 4. a dead owner=cli entry disappears without anything being killed --------


def test_a_dead_cli_entry_disappears_without_killing_anything(tracker, kills):
    """Rule 2 protects *live* processes: there is nothing to protect in one that
    is already gone, and leaving it behind is keeping exactly the garbage this
    came to clean. The process is not touched either way — a dead process cannot
    be."""
    _track(tracker, DEAD_PID, owner=OWNER_CLI)

    result = reconcile.reconcile()

    assert kills == []
    assert _pids() == []
    assert result["ghosts"] == 1


# --- 5. it runs before uvicorn, and a failure costs nothing --------------------


def test_reconciliation_runs_before_uvicorn(monkeypatch):
    """With `lifespan="off"` there is no startup hook: `run.py` is the only place
    this can come from, and moving it inside the server would make it late —
    after the first request has already read the dirty registry."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    order: list[str] = []
    monkeypatch.setattr(reconcile, "reconcile", lambda *a, **k: order.append("reconcile"))
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: order.append("warm"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: order.append("uvicorn"))

    web_run.run(open_browser=False)

    assert order == ["reconcile", "warm", "uvicorn"]


def test_a_failure_inside_reconciliation_never_stops_the_server(monkeypatch):
    """A broken cleanup must not cost the developer their UI."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    def boom():
        raise RuntimeError("the tracker directory cannot be read")

    started = []
    monkeypatch.setattr(reconcile, "reconcile", boom)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: started.append(k))

    web_run.run(open_browser=False)

    assert started, "uvicorn never ran"


# --- the rules on their own, with no OS and no tracker -------------------------


def _decide(entry, *, alive, supervisor_alive):
    decisions = plan([entry], lambda pid: alive, lambda pid: supervisor_alive)
    return [(d.action, d.reason) for d in decisions]


def _entry(**overrides):
    return {
        "pid": 4242,
        "owner": OWNER_WEB,
        "supervisor": None,
        "label": "Kafka Server",
        "local_port": 9092,
        **overrides,
    }


def test_the_plan_kills_the_orphan_and_only_the_orphan():
    assert _decide(_entry(supervisor=GONE_PID), alive=True, supervisor_alive=False) == [
        (KILL, REASON_ORPHAN)
    ]


def test_the_plan_leaves_what_a_live_session_is_still_supervising():
    assert _decide(_entry(supervisor=99), alive=True, supervisor_alive=True) == [
        (KEEP, REASON_LIVE)
    ]


def test_the_plan_leaves_a_live_foreign_entry_alone():
    assert _decide(_entry(owner=OWNER_CLI), alive=True, supervisor_alive=False) == [
        (KEEP, REASON_FOREIGN)
    ]


def test_the_plan_forgets_the_dead_wherever_they_came_from():
    assert _decide(_entry(), alive=False, supervisor_alive=True) == [(UNTRACK, REASON_GHOST)]
    assert _decide(_entry(owner=OWNER_CLI), alive=False, supervisor_alive=True) == [
        (UNTRACK, REASON_GHOST)
    ]


def test_the_plan_returns_one_decision_per_entry():
    entries = [_entry(pid=1), _entry(pid=2, owner=OWNER_CLI), _entry(pid=3, supervisor=99)]

    decisions = plan(entries, lambda pid: pid != 3, lambda pid: False)

    assert [d.pid for d in decisions] == [1, 2, 3]


# --- what the log leaves behind ------------------------------------------------


def test_one_line_per_decision_and_one_summary(tracker, kills):
    """The summary line is what later answers "why did the web not stop that".
    Without it, a strange row on screen has nobody to ask."""
    _track(tracker, DEAD_PID, owner=OWNER_WEB, local_port=9092)
    _track(tracker, os.getpid(), owner=OWNER_CLI, local_port=9093)

    printed = _capture(reconcile.reconcile)
    lines = [line for line in printed.splitlines() if "reconciliación" in line]

    assert len(lines) == 3, printed  # two decisions plus the summary
    flat = _flat(printed)
    assert "lo levantó la terminal, no la web" in flat
    # `intactos` counts entries belonging to a *live web session* only. Counting
    # the foreign ones here as well made the buckets overlap, and the summary
    # reported 2 decisions for 2 entries as "1 intactos ... 1 ajenos sin tocar"
    # — a tally that does not add up to the number of entries.
    assert "0 intactos" in flat
    assert "1 fantasmas olvidados" in flat
    assert "1 ajenos sin tocar" in flat


def test_an_empty_tracker_says_nothing(tracker):
    """Every `yappy web` pays this line, and an empty tracker is the normal case,
    not news."""
    assert _capture(reconcile.reconcile) == ""


def test_one_unreadable_entry_does_not_abandon_the_others(tracker):
    """A file that cannot be removed is a problem with *that* entry. Everything
    else still gets resolved."""
    original = process_tracker.untrack_process
    calls = []

    def untrack(pid):
        calls.append(pid)
        if pid == DEAD_PID:
            raise OSError("file locked by another process")
        return original(pid)

    process_tracker.untrack_process = untrack
    try:
        _track(tracker, DEAD_PID, owner=OWNER_WEB)
        _track(tracker, DEAD_PID + 1, owner=OWNER_WEB)
        printed, result = _capture_result(reconcile.reconcile)
    finally:
        process_tracker.untrack_process = original

    assert sorted(calls) == [DEAD_PID, DEAD_PID + 1]
    assert result["failed"] == 1
    assert result["ghosts"] == 1
    assert "sin poder decidir" in _flat(printed)


def test_el_resumen_cuadra_con_las_entradas(tracker, kills):
    """The summary line exists so the log can be checked against the lines above it.

    Its buckets used to overlap: `intactos` counted the foreign entries as well
    as the ones belonging to a live web session, so three entries summarised as
    four. A tally that does not add up is worse than none — it invites you to
    trust it. The property worth pinning is that the buckets are disjoint and
    cover every entry exactly once, not the exact wording.
    """
    # One entry per bucket, all four at once: a live web-owned one, a live
    # foreign one, and one ghost of each owner.
    _track(tracker, os.getpid(), owner=OWNER_WEB, supervisor=os.getpid())
    _track(tracker, os.getpid(), owner=OWNER_CLI, local_port=9093)
    _track(tracker, DEAD_PID, owner=OWNER_WEB, supervisor=GONE_PID, local_port=9094)
    _track(tracker, DEAD_PID + 1, owner=OWNER_CLI, local_port=9095)

    total = len(process_tracker.get_tracked_processes())
    printed, resultado = _capture_result(reconcile.reconcile)

    suma = (
        resultado["kept"]
        + resultado["killed"]
        + resultado["ghosts"]
        + resultado["foreign"]
        + resultado["refused"]
        + resultado["failed"]
    )
    assert suma == total, (suma, total, resultado)

    flat = _flat(printed)
    # The printed line has to agree with the returned numbers, and in particular
    # must not repeat a foreign entry under `intactos` as well.
    assert f"{resultado['kept']} intactos" in flat, flat
    assert f"{resultado['foreign']} ajenos sin tocar" in flat, flat
