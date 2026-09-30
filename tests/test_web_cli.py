"""Tests de `yappy web` y `yappy stop web`.

Lo critico aqui no es que arranque, sino que el web NO se confunda con los
tunnels del CLI: si el web trackeara como resource="tunnel", un
`yappy ssm kill` mataria los procesos del web (y al reves).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import cli.verbs.stop as stop
import cli.verbs.web as web


class FakeProc:
    def __init__(self, pid=4242, returncode=None):
        self.pid = pid
        self._returncode = returncode

    def poll(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0


@pytest.fixture
def tracked(monkeypatch, tmp_path):
    """Redirect the process tracker to a temp dir."""
    monkeypatch.setattr(web.process_tracker, "_TRACKER_DIR", tmp_path / "tracker")
    return tmp_path / "tracker"


@pytest.fixture
def live():
    """A genuinely running child process.

    `get_tracked_processes` probes liveness with `os.kill(pid, 0)`, so a made-up
    PID is reported as `alive=False` and `stop web` treats it as a stale entry
    instead of killing it. Tests about killing need a real process.

    Spawned in its own session, exactly like `_spawn` does, so exercising the
    real `os.killpg()` path doesn't kill pytest along with it.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=(sys.platform != "win32"),
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


# --- ports ---------------------------------------------------------------


def test_web_uses_centralized_ports(monkeypatch, tmp_path):
    from web.api.ports_registry import YappyPort

    cmd = web._api_command(reload=False)
    assert str(YappyPort.WEB_API) in cmd
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1", "must never bind 0.0.0.0"

    ng = tmp_path / "ng.js"
    ng.write_text("//")
    monkeypatch.setattr(web, "_NG_BIN", ng)
    monkeypatch.setattr(web, "_node_executable", lambda: "node")

    ui = web._ui_command()
    assert str(YappyPort.WEB_UI_DEV) in ui
    assert ui[ui.index("--host") + 1] == "127.0.0.1"
    assert ui[1] == str(ng), "must invoke ng.js directly, not via npx/npm"


def test_reload_flag_is_passed_through():
    assert "--reload" in web._api_command(reload=True)
    assert "--reload" not in web._api_command(reload=False)


# --- tracking isolation --------------------------------------------------


def test_web_processes_are_tracked_as_web_not_tunnel(tracked):
    web._spawn([sys.executable, "-c", "import time; time.sleep(30)"], "API")

    entries = [
        json.loads(p.read_text())
        for p in tracked.glob("*.json")
    ]
    assert len(entries) == 1
    assert entries[0]["resource"] == "web", (
        "tracking as 'tunnel' would let `yappy ssm kill` take the web down"
    )
    assert entries[0]["target"] == "API"


def test_stop_web_never_touches_tunnels(tracked, monkeypatch, live):
    killed = []
    monkeypatch.setattr(web, "_kill_tree", lambda pid: killed.append(pid))

    tunnel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=(sys.platform != "win32"),
    )
    web.process_tracker.track_process(pid=tunnel.pid, resource="tunnel", target="dev")
    web.process_tracker.track_process(pid=live.pid, resource="web", target="API")

    try:
        from typer.testing import CliRunner
        from cli.cli import app

        CliRunner().invoke(app, ["stop", "web"])

        assert killed == [live.pid], "only the web process should be killed"
        assert tunnel.poll() is None, "the SSM tunnel must survive `yappy stop web`"
    finally:
        tunnel.kill()
        tunnel.wait(timeout=5)


def test_spawn_isolates_the_process_group(monkeypatch):
    """Regression: without start_new_session, os.killpg() in _kill_tree would
    kill `yappy web` and the user's shell along with the server, because the
    children inherit our process group."""
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(web.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(web.sys, "platform", "linux")
    web._spawn(["whatever"], "API")

    assert seen.get("start_new_session") is True


def test_stop_web_actually_kills_the_process(tracked, live):
    """No monkeypatching of _kill_tree: verify the real signal path.

    Asserted via returncode, not os.kill(pid, 0): a killed child stays a zombie
    until the parent reaps it, and the pid still resolves for a zombie.
    """
    from typer.testing import CliRunner
    from cli.cli import app

    web.process_tracker.track_process(pid=live.pid, resource="web", target="API")
    CliRunner().invoke(app, ["stop", "web"])

    try:
        live.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("stop web did not actually kill the process")
    assert live.returncode is not None


def test_stop_web_cleans_stale_entries(tracked, monkeypatch):
    """A dead PID must be untracked, not reported as stopped."""
    killed = []
    monkeypatch.setattr(web, "_kill_tree", lambda pid: killed.append(pid))
    web.process_tracker.track_process(pid=999999, resource="web", target="API")

    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["stop", "web"])

    assert killed == []
    assert "already dead" in result.output
    assert not tracked.exists() or not list(tracked.glob("*.json"))


def test_stop_web_with_nothing_running_is_not_an_error():
    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["stop", "web"])
    assert result.exit_code == 0


# --- command wiring ------------------------------------------------------


def test_no_api_and_no_ui_together_dies_before_spawning(monkeypatch):
    spawned = []
    monkeypatch.setattr(web, "_spawn", lambda *a, **k: spawned.append(a))

    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["web", "--no-api", "--no-ui"])

    assert result.exit_code != 0
    assert spawned == [], "must validate flags before spawning anything"


def test_web_degrades_to_api_only_when_frontend_missing(monkeypatch, tmp_path):
    """Repo state today: no web/frontend yet. `yappy web` must still start the API."""
    monkeypatch.setattr(web, "_NG_BIN", tmp_path / "nope" / "ng.js")
    spawned = []
    # The API "exits" so the supervision loop terminates.
    monkeypatch.setattr(
        web,
        "_spawn",
        lambda cmd, name, cwd=None: spawned.append(name) or FakeProc(returncode=0),
    )

    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["web", "--no-reload"])

    assert spawned == ["API"]
    assert "not scaffolded" in result.output


def test_web_starts_both_when_frontend_ready(monkeypatch, tmp_path):
    ng = tmp_path / "ng.js"
    ng.write_text("//")
    monkeypatch.setattr(web, "_NG_BIN", ng)
    monkeypatch.setattr(web, "_node_executable", lambda: "node")

    spawned = []
    # The UI "exits" so the supervision loop terminates after both started.
    queue = [FakeProc(), FakeProc(returncode=1)]
    monkeypatch.setattr(
        web,
        "_spawn",
        lambda cmd, name, cwd=None: spawned.append((name, cwd)) or queue.pop(0),
    )

    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["web", "--no-reload"])

    assert [name for name, _ in spawned] == ["API", "UI"]
    assert spawned[1][1] == web._FRONTEND_DIR, "UI must run inside web/frontend"
    assert "8300" in result.output and "4300" in result.output
    assert "UI exited" in result.output, "a dead service must tear the other down"


def test_web_stops_everything_on_interrupt(monkeypatch, tmp_path):
    ng = tmp_path / "ng.js"
    ng.write_text("//")
    monkeypatch.setattr(web, "_NG_BIN", ng)
    monkeypatch.setattr(web, "_node_executable", lambda: "node")

    procs = [FakeProc(1), FakeProc(2)]
    monkeypatch.setattr(web, "_spawn", lambda cmd, name, cwd=None: procs.pop(0))

    killed = []
    monkeypatch.setattr(web, "_kill_tree", lambda pid: killed.append(pid))

    def boom(_s):
        raise KeyboardInterrupt

    monkeypatch.setattr(web.time, "sleep", boom)

    from typer.testing import CliRunner
    from cli.cli import app

    result = CliRunner().invoke(app, ["web", "--no-reload"])

    assert killed == [1, 2], "Ctrl+C must take down both, not orphan the UI"
    assert "Shutting down" in result.output


def test_web_does_not_spawn_ui_when_no_ui_flag(monkeypatch, tmp_path):
    ng = tmp_path / "ng.js"
    ng.write_text("//")
    monkeypatch.setattr(web, "_NG_BIN", ng)

    spawned = []
    monkeypatch.setattr(
        web,
        "_spawn",
        lambda cmd, name, cwd=None: spawned.append(name) or FakeProc(returncode=0),
    )

    from typer.testing import CliRunner
    from cli.cli import app

    CliRunner().invoke(app, ["web", "--no-ui", "--no-reload"])

    assert spawned == ["API"]


def test_ui_command_refuses_without_angular_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "_NG_BIN", tmp_path / "missing" / "ng.js")

    with pytest.raises(SystemExit):
        web._ui_command()


def test_kill_tree_on_posix_kills_the_process_group(monkeypatch):
    calls = []
    monkeypatch.setattr(web.sys, "platform", "linux")
    monkeypatch.setattr(web.os, "killpg", lambda pgid, sig: calls.append(("killpg", pgid, sig)))
    monkeypatch.setattr(web.os, "getpgid", lambda pid: pid)

    web._kill_tree(777)

    assert calls == [("killpg", 777, web.signal.SIGTERM)]


def test_kill_tree_on_windows_uses_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(web.sys, "platform", "win32")
    monkeypatch.setattr(
        web.subprocess, "run", lambda cmd, **kw: calls.append(cmd)
    )

    web._kill_tree(777)

    assert calls == [["taskkill", "/pid", "777", "/t", "/f"]]
