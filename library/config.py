from __future__ import annotations

import os
import glob
from pathlib import Path
from dotenv import dotenv_values


class Config:
    _config_dir: Path | None = None

    def __init__(self, env: str | None = None):
        self._env = env
        self._values: dict[str, str] = {}

        config_dir = self._get_config_dir()
        self._load_file(config_dir / "env.base")
        if env:
            env_file = config_dir / f"env.{env}"
            if env_file.exists():
                self._load_file(env_file)
            else:
                available = self.known_environments()
                raise ValueError(
                    f"No config for environment '{env}'. "
                    f"Available: {', '.join(available) if available else 'none'}"
                )

        # Also load local .env from project root or cwd
        local_env = config_dir.parent / ".env"
        if local_env.exists():
            self._load_file(local_env)

    @classmethod
    def _get_config_dir(cls) -> Path:
        if cls._config_dir:
            return cls._config_dir
        package_root = Path(__file__).resolve().parent.parent
        config_dir = package_root / "config"
        if config_dir.exists():
            cls._config_dir = config_dir
            return config_dir
        fallback = Path.cwd() / "config"
        if fallback.exists():
            cls._config_dir = fallback
            return fallback
        return config_dir

    @classmethod
    def with_env(cls, env: str) -> Config:
        return cls(env=env)

    def _load_file(self, path: Path):
        if not path.exists():
            return
        values = dotenv_values(path)
        if values:
            self._values.update(values)

    @classmethod
    def known_environments(cls) -> list[str]:
        config_dir = cls._get_config_dir()
        pattern = str(config_dir / "env.*")
        files = glob.glob(pattern)
        envs = []
        for f in files:
            parts = Path(f).name.split(".")
            if len(parts) == 2 and parts[0] == "env" and parts[1] != "base":
                envs.append(parts[1])
        return sorted(envs)

    def get(self, key: str, default: str | None = None) -> str | None:
        # Precedence: (1) config files, (2) explicit YAPPY_* overrides,
        # (3) plain os.environ as a last resort when the key is not defined
        # in any config file.
        if key in self._values:
            return self._values[key]
        yappy_val = os.environ.get(f"YAPPY_{key}")
        if yappy_val is not None:
            return yappy_val
        return os.environ.get(key, default)

    def require(self, key: str) -> str:
        val = self.get(key)
        if val is None:
            raise ValueError(
                f"Missing required config: {key} "
                f"(check config/env.{self._env or 'base'} "
                f"or set YAPPY_{key})"
            )
        return val

    @property
    def env(self) -> str | None:
        return self._env

    @property
    def profile(self) -> str:
        return self.get("AWS_PROFILE", "base-profile")

    @property
    def region(self) -> str:
        return self.get("AWS_REGION", "us-west-2")

    @property
    def instance(self) -> str | None:
        return self.get("AWS_INSTANCE")

    @property
    def host(self) -> str | None:
        return self.get("AWS_HOST")

    @property
    def cluster(self) -> str | None:
        return self.get("AWS_CLUSTER")

    @property
    def db_port(self) -> int:
        return int(self.get("DB_PORT", "8100"))

    @property
    def aws_port(self) -> int:
        return int(self.get("AWS_PORT", "53360"))

    @property
    def kafka_path(self) -> str:
        # Project-local by default: <project_root>/devkit/kafka
        # Override with KAFKA_PATH env var if needed
        default = str(Path(__file__).resolve().parent.parent / "devkit" / "kafka")
        return self.get("KAFKA_PATH", default)

    @property
    def kafka_core_path(self) -> str:
        return str(Path(self.kafka_path) / "kafka-core")

    @property
    def kafka_ui_path(self) -> str:
        return str(Path(self.kafka_path) / "kafka-ui")

    @property
    def kafka_config_server(self) -> str:
        return str(Path(self.kafka_path) / "config" / "server")

    @property
    def kafka_config_ui(self) -> str:
        return str(Path(self.kafka_path) / "config" / "ui")

    @property
    def profile_path(self) -> str:
        return self.get("PROFILE_PATH", "C:\\Development\\profile")

    @property
    def workspace_path(self) -> str:
        return self.require("WORKSPACE_PATH")

    @property
    def aws_user(self) -> str | None:
        return self.get("AWS_USER")
