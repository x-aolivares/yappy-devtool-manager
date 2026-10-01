"""Setup owns dependency installation (single source of truth).

install.sh must stay bootstrap-only: no npm, no dep list. These tests lock
that contract in so the two cannot drift apart silently.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import yappy_cli.cli as cli

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def _record(calls):
    def _run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    return _run


def test_install_frontend_deps_runs_npm_install_in_frontend(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm")

    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    cli._install_frontend_deps(fe)

    assert len(calls) == 1
    assert calls[0][0] == "npm"
    assert calls[0][1] == "install", "must use npm install, not npm ci"
    assert calls[0][2:] == [], "no extra args expected"


def test_install_frontend_deps_uses_npm_install_not_ci_even_with_lockfile(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm")

    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    (fe / "package-lock.json").write_text("{}")
    cli._install_frontend_deps(fe)

    assert calls[0][1] == "install"


def test_install_frontend_deps_warns_when_npm_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    cli._install_frontend_deps(fe)

    assert "npm not found" in capsys.readouterr().out


def test_install_frontend_deps_skips_without_package_json(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm")

    fe = tmp_path / "frontend"
    fe.mkdir()
    cli._install_frontend_deps(fe)

    assert calls == []
    assert "package.json not found" in capsys.readouterr().out


def test_install_frontend_deps_never_dies_on_failure(monkeypatch, tmp_path, capsys):
    """A failed npm install must warn, not abort the rest of setup."""
    monkeypatch.setattr(cli.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr="boom", stdout=""),
    )

    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    cli._install_frontend_deps(fe)  # must not raise SystemExit

    assert "Frontend install failed" in capsys.readouterr().out


def test_install_sh_is_bootstrap_only():
    """install.sh must not duplicate dependency logic from yappy setup."""
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "npm " not in text, "install.sh must not install frontend deps; yappy setup owns that"
    assert "requirements.txt" not in text, "install.sh must not enumerate Python deps"
    assert "node_modules" not in text


def test_install_sh_uses_python_m_pip_not_bare_pip():
    """Bare `pip` can resolve to a different interpreter than the script's python."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "python -m pip install -e ." in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("pip "):
            raise AssertionError(f"bare pip found in install.sh: {stripped}")


def test_install_sh_points_to_setup_for_dependencies():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "yappy setup" in text


def test_backend_deps_install_targets_running_interpreter(monkeypatch, tmp_path):
    """Must use `python -m pip`, so deps land in the env that runs yappy."""
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))

    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    cli._install_backend_deps(root)

    assert len(calls) == 1
    argv = calls[0]
    assert argv[:4] == [sys.executable, "-m", "pip", "install"]
    assert argv[4] == "-e"
    assert argv[5] == str(root)


def test_backend_deps_install_skips_without_pyproject(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", _record(calls))

    root = tmp_path / "not-a-repo"
    root.mkdir()
    cli._install_backend_deps(root)

    assert calls == []
    assert "pyproject.toml not found" in capsys.readouterr().out


def test_backend_deps_install_warns_on_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stderr="boom", stdout=""),
    )

    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    cli._install_backend_deps(root)  # must not raise SystemExit

    assert "Backend install failed" in capsys.readouterr().out
