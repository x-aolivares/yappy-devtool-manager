from __future__ import annotations

import os
import glob
import re
import sys
from pathlib import Path
from dotenv import dotenv_values

from .paths import project_config_dir, project_root


def win_to_posix(path: str) -> str:
    """Convert a Windows path to Git Bash style for display (C:\\x -> /c/x)."""
    p = path.replace("\\", "/")
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:/", p):
        p = f"/{p[0].lower()}{p[2:]}"
    return p


def posix_to_win(path: str) -> str:
    """Normalize a Git Bash style path (/c/x) to a real Windows path (C:\\x).

    Accepts both formats; non-posix values pass through unchanged.
    """
    m = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if m:
        return f"{m.group(1).upper()}:{os.sep}{m.group(2).replace('/', os.sep)}".rstrip(os.sep)
    return path


def _package_root_config() -> Path:
    return project_config_dir()


def default_kafka_path() -> str:
    """Where Kafka lives when nothing overrides it: project-local `devkit/kafka`.

    Separate from :attr:`Config.kafka_path` on purpose: this is the *default*, with
    no `KAFKA_PATH` override applied. The docs describe the default, so what a
    given machine happens to override must not decide whether they are right.
    """
    return str(project_root() / "devkit" / "kafka")


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

        # Also load local .env from project root or cwd.
        local_env_root = (
            config_dir.parent.parent
            if config_dir.parent.name == "backend"
            else config_dir.parent
        )
        local_env = local_env_root / ".env"
        if local_env.exists():
            self._load_file(local_env)

    @classmethod
    def _get_config_dir(cls) -> Path:
        if cls._config_dir:
            return cls._config_dir

        # 1) Explicit override (YAPPY_CONFIG_DIR=<folder with env.* files>)
        override = os.environ.get("YAPPY_CONFIG_DIR")
        if override and Path(override).is_dir():
            cls._config_dir = Path(override)
            return cls._config_dir

        # 2) The project this package is installed from (editable installs / source tree)
        package_root = _package_root_config()
        if package_root.is_dir():
            cls._config_dir = package_root
            return cls._config_dir

        # 3) Walk up from cwd looking for backend/config or a legacy config dir.
        #    This keeps `yappy web` (and any command) working no matter where the
        #    package is installed from or which directory it is invoked in.
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / "backend" / "config"
            if (candidate / "env.base").is_file():
                cls._config_dir = candidate
                return cls._config_dir

            candidate = parent / "config"
            if (candidate / "env.base").is_file():
                cls._config_dir = candidate
                return cls._config_dir

        # 4) Last resort: the repository-relative backend config dir.
        cls._config_dir = package_root
        return cls._config_dir

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
        envs = []
        for f in glob.glob(pattern):
            name = Path(f).name[len("env."):]
            if name in ("", "base") or name.endswith(".example"):
                continue
            envs.append(name)
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
                f"(check backend/config/env.{self._env or 'base'} "
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
    def endpoint_url(self) -> str | None:
        return self.get("AWS_ENDPOINT_URL")

    @property
    def is_local(self) -> bool:
        """True when this environment is NOT an AWS environment.

        A local environment is reached directly (a MySQL container over TCP), so
        it has no meaningful region, profile, SSM parameter store or Secrets
        Manager: anything AWS-shaped must be refused instead of silently falling
        back to the inherited `base-profile` credentials.

        The flag is the `DB_MODE=local` key already declared in
        `backend/config/env.local`.
        """
        return (self.get("DB_MODE") or "").strip().lower() == "local"

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
        return self.get("KAFKA_PATH") or default_kafka_path()

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
        # No hardcoded Windows path: derive from the environment. Override with PROFILE_PATH.
        default = str(Path(os.environ.get("SystemDrive", "C:")) / "Development" / "profile")
        return self.get("PROFILE_PATH", default)

    @property
    def workspace_path(self) -> str:
        # Accepts Git Bash style (/c/...) in config; normalize for the Windows runtime.
        return posix_to_win(self.require("WORKSPACE_PATH"))

    @property
    def aws_user(self) -> str | None:
        return self.get("AWS_USER")
