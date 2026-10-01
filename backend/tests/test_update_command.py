import sys
from types import SimpleNamespace

import pytest

import yappy_cli.cli as cli


def _ok(*args, **kwargs):
    return SimpleNamespace(returncode=0, stderr="")


def _record(calls):
    def _run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")
    return _run


def test_update_pulls_and_reinstalls(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))
    monkeypatch.setattr(cli, "version", lambda: None)

    cli.update()

    assert calls[0] == ["git", "pull", "--ff-only"]
    pip_call = calls[1]
    assert pip_call[0] == sys.executable
    assert "-m" in pip_call and "install" in pip_call and "-e" in pip_call
    out = capsys.readouterr().out
    assert "Update complete" in out


def test_update_continues_when_git_pull_fails(monkeypatch, capsys):
    calls = []

    def _pull_fails(cmd, **kw):
        calls.append(cmd)
        if cmd == ["git", "pull", "--ff-only"]:
            return SimpleNamespace(returncode=1, stderr="diverged")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _pull_fails)
    monkeypatch.setattr(cli, "version", lambda: None)

    cli.update()

    assert len(calls) == 2, "pip reinstall must still run after a failed pull"
    out = capsys.readouterr().out
    assert "Update complete" in out


def test_update_fails_hard_when_reinstall_fails(monkeypatch):
    calls = []

    def _install_fails(cmd, **kw):
        calls.append(cmd)
        if "install" in cmd:
            return SimpleNamespace(returncode=1, stderr="boom")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", _install_fails)

    with pytest.raises(SystemExit):
        cli.update()
