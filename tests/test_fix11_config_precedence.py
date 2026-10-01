import pytest

from yappy_library.config import Config

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


def test_known_environments_accepts_names_with_dots(config_dir):
    (config_dir / "env.us-east-1").write_text("AWS_REGION=us-east-1\n")
    (config_dir / "env.qa.bravo").write_text("AWS_REGION=eu-central-1\n")
    (config_dir / "env.environment.example").write_text("AWS_REGION=us-west-1\n")

    assert Config.known_environments() == ["dev", "qa.bravo", "us-east-1"]


def test_known_environments_excludes_base_and_examples(config_dir):
    assert "base" not in Config.known_environments()
    assert all("example" not in e for e in Config.known_environments())


def test_yappy_config_dir_override_wins(tmp_path, monkeypatch):
    alt = tmp_path / "config"
    alt.mkdir()
    (alt / "env.bravo").write_text("AWS_REGION=eu-west-1\n")
    monkeypatch.setenv("YAPPY_CONFIG_DIR", str(alt))
    monkeypatch.setattr(Config, "_config_dir", None)

    assert Config._get_config_dir() == alt
    assert Config.known_environments() == ["bravo"]


def test_config_resolved_walking_up_from_cwd(tmp_path, monkeypatch):
    from yappy_library import config as config_mod

    cfg_dir = tmp_path / "proyecto" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "env.base").write_text("AWS_PROFILE=base\n")
    (cfg_dir / "env.promo").write_text("AWS_REGION=sa-east-1\n")
    subdir = tmp_path / "proyecto" / "subdir"
    subdir.mkdir()

    monkeypatch.chdir(subdir)
    monkeypatch.setattr(Config, "_config_dir", None)
    monkeypatch.setattr(
        config_mod,
        "_package_root_config",
        lambda: tmp_path / "no-existe" / "config",
    )

    assert Config._get_config_dir() == cfg_dir
    assert Config.known_environments() == ["promo"]
