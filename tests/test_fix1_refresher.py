import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import yappy_cli.db.tunnel as tunnel
import yappy_cli.process_tracker as process_tracker


def _fake_cfg():
    required = {
        "AWS_INSTANCE": "i-123",
        "AWS_PORT": "53360",
        "AWS_REGION": "us-east-1",
        "AWS_HOST": "db.example.com",
        "AWS_USER": "app",
    }
    return SimpleNamespace(
        profile="dev",
        db_port=8100,
        get=lambda key, default=None: {"AWS_PORT": "53360"}.get(key, default),
        require=lambda key: required[key],
    )


def _fake_proc():
    return SimpleNamespace(
        pid=1234,
        poll=lambda: None,
        terminate=lambda: None,
        wait=lambda timeout=0: None,
        returncode=0,
    )


@pytest.fixture(autouse=True)
def _common_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(tunnel, "_generate_token", lambda cfg: "tok")
    monkeypatch.setattr(tunnel, "_write_local_env", lambda token: None)
    monkeypatch.setattr(tunnel.Config, "with_env", staticmethod(lambda env: _fake_cfg()))
    monkeypatch.setattr(tunnel.db_cmd, "check_requirements", lambda *a, **k: None)
    monkeypatch.setattr(process_tracker, "track_process", lambda *a, **k: None)
    monkeypatch.setattr(process_tracker, "untrack_process", lambda pid: None)


def test_detach_spawns_refresher_child_process(monkeypatch, tmp_path, capsys):
    popen_calls = []

    def fake_popen(cmd, *args, **kwargs):
        popen_calls.append((cmd, kwargs))
        return SimpleNamespace(pid=9999)

    monkeypatch.setattr(tunnel.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tunnel.db_cmd, "ssm_tunnel", lambda **kw: _fake_proc())
    monkeypatch.setattr(tunnel.time, "sleep", lambda s: None)
    refresher_calls = []
    monkeypatch.setattr(tunnel, "_start_refresher", lambda *a, **k: refresher_calls.append(1))

    tunnel.up("dev", auto_refresh=True, detach=True, keep_alive=False)

    assert popen_calls, "expected a detached child process to be spawned"
    cmd, kwargs = popen_calls[-1]
    assert cmd[:3] == [sys.executable, "-m", "yappy_cli.db.refresher"]
    assert cmd[3:] == ["dev"]
    assert "db-refresher-dev.log" in str(kwargs["stdout"])
    assert (tmp_path / ".yappy" / "logs" / "db-refresher-dev.log").exists()
    assert not refresher_calls, "detached path must not start the in-process thread refresher"

    out = capsys.readouterr().out
    assert "Auto-refresh tunnel running in background" in out


def test_non_detach_still_starts_tunnel_loop(monkeypatch, capsys):
    monkeypatch.setattr(tunnel.db_cmd, "ssm_tunnel", lambda **kw: _fake_proc())
    monkeypatch.setattr(
        tunnel.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("non-detach must not spawn a child")),
    )
    monkeypatch.setattr(tunnel.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()))
    killed = []
    monkeypatch.setattr(tunnel.db_cmd, "kill_ssm", lambda *a, **k: killed.append(1))
    refresher_calls = []
    monkeypatch.setattr(tunnel, "_start_refresher", lambda *a, **k: refresher_calls.append(1))

    tunnel.up("dev", auto_refresh=True, detach=False, keep_alive=False)

    assert refresher_calls, "non-detach path must keep the thread refresher"
    assert killed, "non-detach Ctrl+C must stop the tunnel"
    out = capsys.readouterr().out
    assert "Press Ctrl+C to stop tunnel and refresher" in out


def test_refresher_module_exposes_main():
    import yappy_cli.db.refresher as refresher

    assert callable(refresher.main)
    assert refresher.REFRESH_INTERVAL == 12 * 60
