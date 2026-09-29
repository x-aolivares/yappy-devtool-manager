import pytest

from library.config import Config

_RELEVANT_KEYS = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_CLUSTER",
    "DB_PORT",
    "KAFKA_PATH",
    "YAPPY_AWS_PROFILE",
    "YAPPY_AWS_REGION",
    "YAPPY_AWS_CLUSTER",
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "env.base").write_text("AWS_PROFILE=base-profile\n")
    (config_dir / "env.dev").write_text("AWS_PROFILE=dev-profile\n")
    monkeypatch.setattr(Config, "_config_dir", config_dir)
    return config_dir


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _RELEVANT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_config_file_wins_over_shell_env(config_dir, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "personal")

    cfg = Config.with_env("dev")

    assert cfg.profile == "dev-profile"


def test_yappy_override_maps_to_aws_region(config_dir, monkeypatch):
    monkeypatch.setenv("YAPPY_AWS_REGION", "xx")

    cfg = Config.with_env("dev")

    assert cfg.region == "xx"


def test_yappy_override_beats_plain_env(config_dir, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("YAPPY_AWS_REGION", "xx")

    cfg = Config.with_env("dev")

    assert cfg.region == "xx"


def test_plain_env_used_only_when_key_not_in_config(config_dir, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    cfg = Config.with_env("dev")

    assert cfg.region == "eu-west-1"


def test_key_in_neither_file_nor_env_falls_back_to_default(config_dir):
    cfg = Config.with_env("dev")

    assert cfg.cluster is None
    assert cfg.get("AWS_CLUSTER") is None


def test_property_default_when_key_absent_everywhere(config_dir):
    cfg = Config.with_env("dev")

    assert cfg.region == "us-west-2"


def test_base_config_profile_still_resolves(config_dir, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "personal")

    cfg = Config()

    assert cfg.profile == "base-profile"
