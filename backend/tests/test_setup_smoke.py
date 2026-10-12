"""Smoke test for the whole `yappy setup` flow.

setup() was crashing at step 1 on an undefined name (`_win_to_posix`), which
meant the command could never complete. Testing the dependency helpers in
isolation did not catch it, because the crash was in a different step. This
test drives setup() end to end with every side effect stubbed, so any step
raising (NameError, AttributeError, bad argv) fails the suite.

Nothing here may touch the real filesystem, the real ~/.bashrc, or the network.
"""

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

import yappy_cli.cli as cli


def _stub_everything(monkeypatch, tmp_path):
    """Neutralize all of setup()'s side effects."""

    repo_root = tmp_path / "repo"
    (repo_root / "backend" / "config").mkdir(parents=True)
    (repo_root / "frontend").mkdir()
    (repo_root / "frontend" / "package.json").write_text("{}")
    (repo_root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    monkeypatch.setattr(cli, "get_project_root", lambda: repo_root)

    # Never touch the developer's real ~/.bashrc.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    calls = []

    def _run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cli.subprocess, "run", _run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")

    # Decline every interactive prompt (config examples, wrapper replacement).
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "n")

    # Kafka download is a real network fetch — stub it.
    import yappy_library.adapters.kafka.setup as kafka_setup

    monkeypatch.setattr(kafka_setup, "setup_kafka_configs", lambda cfg: None)
    monkeypatch.setattr(kafka_setup, "setup_kafka", lambda cfg: True)

    monkeypatch.setattr(
        cli, "Config", lambda *a, **k: SimpleNamespace(profile="test-profile")
    )

    return calls


def test_setup_runs_to_completion(monkeypatch, tmp_path, capsys):
    """setup() must reach its final line without raising."""
    _stub_everything(monkeypatch, tmp_path)

    cli.setup()

    out = capsys.readouterr().out
    assert "Setup complete" in out, f"setup() did not finish. Output was:\n{out}"


def test_setup_does_not_crash_on_any_step(monkeypatch, tmp_path, capsys):
    """Guards the whole body: a fresh .bashrc exercises the write branches."""
    _stub_everything(monkeypatch, tmp_path)

    # Empty .bashrc -> setup must create it and append PATH + shell integration.
    (tmp_path / ".bashrc").write_text("")

    cli.setup()

    out = capsys.readouterr().out
    assert "Setup complete" in out
    assert "Traceback" not in out and "NameError" not in out


def test_setup_runs_when_bashrc_missing_entirely(monkeypatch, tmp_path, capsys):
    _stub_everything(monkeypatch, tmp_path)
    assert not (tmp_path / ".bashrc").exists()

    cli.setup()

    assert "Setup complete" in capsys.readouterr().out


def test_setup_installs_backend_and_frontend_deps(monkeypatch, tmp_path, capsys):
    calls = _stub_everything(monkeypatch, tmp_path)
    (tmp_path / ".bashrc").write_text("")

    cli.setup()

    joined = [" ".join(c) for c in calls]
    assert any("pip install -e" in c for c in joined), "backend editable install missing"
    assert any(c.endswith("npm install") or " npm install" in c for c in joined), (
        "frontend npm install missing"
    )


def test_setup_is_idempotent(monkeypatch, tmp_path, capsys):
    """Running setup twice must not corrupt .bashrc."""
    _stub_everything(monkeypatch, tmp_path)

    cli.setup()
    first = (tmp_path / ".bashrc").read_text(encoding="utf-8")
    capsys.readouterr()

    cli.setup()
    second = (tmp_path / ".bashrc").read_text(encoding="utf-8")

    assert "Setup complete" in capsys.readouterr().out
    assert first.count('eval "$(yappy init bash)"') == second.count(
        'eval "$(yappy init bash)"'
    ), "second run duplicated the shell integration line"
    assert first.count("Python Scripts on PATH") == second.count("Python Scripts on PATH")


# --- --front / --back ------------------------------------------------------
#
# Los flags parten el comando en dos mitades. El riesgo no es que una mitad
# falle, es que la otra se cuele: un `--front` que toca el .bashrc del
# desarrollador, o un `--back` que dispara un npm install de tres minutos, son
# los dos bugs que hay que cazar.


def test_setup_without_flags_does_both_halves(monkeypatch, tmp_path, capsys):
    calls = _stub_everything(monkeypatch, tmp_path)

    cli.setup(front=False, back=False)

    joined = [" ".join(c) for c in calls]
    assert any("pip install -e" in c for c in joined)
    assert any("npm install" in c for c in joined)


def test_setup_front_only_installs_frontend_deps(monkeypatch, tmp_path, capsys):
    calls = _stub_everything(monkeypatch, tmp_path)
    (tmp_path / ".bashrc").write_text("")

    cli.setup(front=True, back=False)

    joined = [" ".join(c) for c in calls]
    assert any("npm install" in c for c in joined), "frontend deps missing"
    assert not any("pip install -e" in c for c in joined), "--front touched the backend"

    # Nada de shell, config, Kafka ni perfil AWS.
    assert (tmp_path / ".bashrc").read_text() == "", "--front wrote to .bashrc"
    out = capsys.readouterr().out
    assert "Kafka:" not in out
    assert "AWS profile:" not in out
    assert "Config files:" not in out


def test_setup_back_only_installs_backend_deps(monkeypatch, tmp_path, capsys):
    calls = _stub_everything(monkeypatch, tmp_path)
    (tmp_path / ".bashrc").write_text("")

    cli.setup(front=False, back=True)

    joined = [" ".join(c) for c in calls]
    assert any("pip install -e" in c for c in joined), "backend deps missing"
    assert not any("npm install" in c for c in joined), "--back ran npm install"

    out = capsys.readouterr().out
    assert "Kafka:" in out
    assert "AWS profile:" in out
    assert "Python Scripts on PATH" in (tmp_path / ".bashrc").read_text()


def test_setup_front_does_not_migrate_backend_resources(monkeypatch, tmp_path):
    """La migración de build/, config/ y data/ es del backend, no de la web."""
    _stub_everything(monkeypatch, tmp_path)
    migrated = []
    monkeypatch.setattr(cli, "migrate_backend_resources", lambda root: migrated.append(root))

    cli.setup(front=True, back=False)

    assert migrated == []


def test_setup_rejects_both_flags(monkeypatch, tmp_path):
    calls = _stub_everything(monkeypatch, tmp_path)

    with pytest.raises(SystemExit):
        cli.setup(front=True, back=True)

    assert calls == [], "no debe instalar nada cuando los flags se contradicen"


def test_setup_final_message_matches_scope(monkeypatch, tmp_path, capsys):
    """`--front` no debe prometer 'source ~/.bashrc': no toco el .bashrc."""
    _stub_everything(monkeypatch, tmp_path)

    cli.setup(front=True, back=False)
    front_out = capsys.readouterr().out
    assert "Frontend setup complete" in front_out
    assert "source ~/.bashrc" not in front_out

    cli.setup(front=False, back=True)
    assert "Setup complete" in capsys.readouterr().out
