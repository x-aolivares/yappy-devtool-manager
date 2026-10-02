from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_read_version_from_pyproject():
    from yappy_cli.cli import _read_version

    assert _read_version() == _pyproject_version()


def test_version_command_falls_back_to_pyproject(monkeypatch, capsys):
    import importlib.metadata as md

    from yappy_cli.cli import version

    def _boom(distribution_name):
        raise md.PackageNotFoundError(distribution_name)

    monkeypatch.setattr(md, "version", _boom)

    version()

    out = capsys.readouterr().out
    assert f"v{_pyproject_version()}" in out


def test_readme_title_and_docs_fixed():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# yappy-cli-manager")
    assert "Nueva sintaxis (Docker-like)" in readme
    assert "yappy run kafka server -d" in readme
    assert "yappy logs kafka server -f" in readme


def test_readme_kafka_path_matches_config_default():
    """The README must show where Kafka lives by default.

    Checked against `default_kafka_path()`, not `Config().kafka_path`: the latter
    honors `KAFKA_PATH` from `backend/config/env.base`, which is gitignored, so
    the gate would pass or fail depending on the machine that ran it.

    The README states the location relative to the repo on purpose. The absolute
    path of a checkout goes stale the moment somebody moves or re-clones it —
    which is exactly what happened here, and nobody noticed because the test was
    reading the wrong value to begin with.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    from yappy_library.config import default_kafka_path, win_to_posix
    from yappy_library.paths import project_root

    relative = Path(default_kafka_path()).relative_to(project_root()).as_posix()

    assert f"{relative}/kafka-core" in readme
    assert f"{relative}/kafka-ui" in readme
    # Kafka is project-local by default, not installed somewhere else on the box.
    assert "config/kafka" not in readme
    # And no absolute path to this checkout: that is the part that goes stale.
    assert str(project_root()) not in readme
    assert win_to_posix(str(project_root())) not in readme


def test_readme_setup_claims_only_env_base_created():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "crea `env.base`, `env.dev`, `env.qa`" not in readme
    assert "crea `backend/config/env.base`" in readme
