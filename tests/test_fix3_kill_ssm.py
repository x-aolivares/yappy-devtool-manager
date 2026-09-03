from types import SimpleNamespace

import pytest

import yappy_cli.base as base
import yappy_cli.process_tracker as process_tracker


def _win_run_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda cmd, *a, **k: (calls.append(cmd), SimpleNamespace(returncode=0))[1],
    )
    monkeypatch.setattr(base.sys, "platform", "win32")
    return calls


def test_kill_ssm_with_pid_uses_taskkill_pid(monkeypatch):
    calls = _win_run_recorder(monkeypatch)
    untracked = []
    monkeypatch.setattr(process_tracker, "untrack_process", lambda pid: untracked.append(pid))

    base.BaseCommand().kill_ssm(pid=123)

    assert calls == [["taskkill", "/pid", "123", "/t", "/f"]]
    assert all("/im" not in cmd for cmd in calls)
    assert untracked == [123]


def test_kill_ssm_no_tracked_warns_and_does_not_kill(monkeypatch, capsys):
    calls = _win_run_recorder(monkeypatch)
    monkeypatch.setattr(process_tracker, "get_tracked_processes", lambda resource=None, target=None: [])

    base.BaseCommand().kill_ssm()

    assert calls == []
    out = capsys.readouterr().out
    assert "No tracked tunnels found" in out
    assert "ssm kill --all" in out


def test_kill_ssm_tracked_kills_only_tracked(monkeypatch):
    calls = _win_run_recorder(monkeypatch)
    monkeypatch.setattr(
        process_tracker,
        "get_tracked_processes",
        lambda resource=None, target=None: [{"pid": 55}],
    )
    untracked = []
    monkeypatch.setattr(process_tracker, "untrack_process", lambda pid: untracked.append(pid))

    base.BaseCommand().kill_ssm()

    assert calls == [["taskkill", "/pid", "55", "/t", "/f"]]
    assert untracked == [55]


def test_ssm_tunnel_tracks_pid(monkeypatch):
    tracked = []
    monkeypatch.setattr(base, "_aws_cmd", lambda: ["aws"])
    monkeypatch.setattr(base.subprocess, "Popen", lambda cmd, **k: SimpleNamespace(pid=777))
    monkeypatch.setattr(process_tracker, "track_process", lambda **kw: tracked.append(kw))

    proc = base.BaseCommand().ssm_tunnel(
        instance="i-1", port=1, local_port=2, region="us-east-1", profile="dev",
    )

    assert proc.pid == 777
    assert tracked == [{"pid": 777, "resource": "tunnel", "target": "dev"}]


def test_serve_untracks_on_exit(monkeypatch, capsys):
    untracked = []
    monkeypatch.setattr(process_tracker, "untrack_process", lambda pid: untracked.append(pid))
    proc = SimpleNamespace(pid=123, wait=lambda: None, terminate=lambda: None)

    base.BaseCommand().serve(proc, detach=False, name="Svc")

    assert untracked == [123]
