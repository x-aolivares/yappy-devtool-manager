from pathlib import Path

import pytest

from yappy_library.paths import migrate_backend_resources, project_config_dir


def test_migration_moves_legacy_resources_and_preserves_existing_files(tmp_path: Path):
    root = tmp_path / "project"
    backend = root / "backend"
    (backend / "config").mkdir(parents=True)
    (backend / "config" / "env.base.example").write_text("AWS_REGION=example\n")

    (root / "config").mkdir()
    (root / "config" / "env.base").write_text("AWS_REGION=local\n")
    (root / "config" / ".idea").mkdir()
    (root / "config" / ".idea" / "workspace.xml").write_text("local settings")

    (root / "data").mkdir()
    (root / "data" / "sessions.db").write_bytes(b"local session database")
    (root / "build" / "lib").mkdir(parents=True)
    (root / "build" / "lib" / "artifact.py").write_text("generated")

    moved = migrate_backend_resources(root)

    assert moved == ("build", "config", "data")
    assert not (root / "build").exists()
    assert not (root / "config").exists()
    assert not (root / "data").exists()
    assert (backend / "build" / "lib" / "artifact.py").read_text() == "generated"
    assert (backend / "config" / "env.base.example").read_text() == "AWS_REGION=example\n"
    assert (backend / "config" / "env.base").read_text() == "AWS_REGION=local\n"
    assert (backend / "config" / ".idea" / "workspace.xml").read_text() == "local settings"
    assert (backend / "data" / "sessions.db").read_bytes() == b"local session database"
    assert migrate_backend_resources(root) == ()


def test_migration_keeps_existing_build_cache_under_backend(tmp_path: Path):
    root = tmp_path / "project"
    (root / "backend" / "build" / "lib").mkdir(parents=True)
    (root / "backend" / "build" / "lib" / "current.py").write_text("current")
    (root / "build" / "lib").mkdir(parents=True)
    (root / "build" / "lib" / "legacy.py").write_text("legacy")

    assert migrate_backend_resources(root) == ("build",)

    assert not (root / "build").exists()
    assert (root / "backend" / "build" / "lib" / "current.py").read_text() == "current"
    assert (
        root / "backend" / "build" / "legacy-root-build" / "lib" / "legacy.py"
    ).read_text() == "legacy"


def test_config_dir_migrates_legacy_config_on_first_use(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    (root / "backend" / "library").mkdir(parents=True)
    (root / "backend" / "config").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='test'\n")
    (root / "config").mkdir()
    (root / "config" / "env.base").write_text("AWS_REGION=local\n")
    monkeypatch.setattr("yappy_library.paths.project_root", lambda: root)

    config_dir = project_config_dir()

    assert config_dir == root / "backend" / "config"
    assert (config_dir / "env.base").read_text() == "AWS_REGION=local\n"
    assert not (root / "config").exists()


def test_migration_refuses_to_overwrite_conflicting_local_files(tmp_path: Path):
    root = tmp_path / "project"
    legacy = root / "config"
    target = root / "backend" / "config"
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    (legacy / "env.base").write_text("AWS_REGION=legacy\n")
    (target / "env.base").write_text("AWS_REGION=backend\n")

    with pytest.raises(FileExistsError, match="would overwrite"):
        migrate_backend_resources(root, names=("config",))

    assert (legacy / "env.base").read_text() == "AWS_REGION=legacy\n"
    assert (target / "env.base").read_text() == "AWS_REGION=backend\n"
