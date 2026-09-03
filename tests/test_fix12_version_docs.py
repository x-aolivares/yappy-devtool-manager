from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


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
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    from yappy_cli.config import Config

    default = Config().kafka_path
    assert "config/kafka" not in readme
    assert default.replace("\\", "\\\\") in readme or default in readme


def test_readme_setup_claims_only_env_base_created():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "crea `env.base`, `env.dev`, `env.qa`" not in readme
    assert "crea `config/env.base`" in readme
