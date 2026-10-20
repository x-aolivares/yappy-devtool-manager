from __future__ import annotations

import os
import glob
import re
import sys
from pathlib import Path
from dotenv import dotenv_values

from .paths import project_config_dir, project_root


def venv_scripts_dir() -> Path:
    """Directorio donde el interprete actual deja los ejecutables entry-point.

    Ese directorio es, por definicion, el padre de `sys.executable`: un venv de
    Windows guarda `Scripts/python.exe` y uno de Unix `bin/python3`, asi que en
    los dos casos el padre ya ES la carpeta que hay que agregar al PATH.

    Antes se armaba a mano como `parent / "Scripts"`, y eso rompia en Linux: la
    carpeta se llama `bin` ahi, el directorio no existia, `setup()` caia al
    `warn()` y no escribia nada en el `.bashrc`. La integracion de shell no se
    instalaba nunca, sin error visible.

    Con un Python de sistema esto devuelve el directorio de binarios del
    sistema, que si existe: agregar `/usr/bin` al PATH no hace dano, y el
    llamador igual avisa cuando el directorio no esta.
    """
    return Path(sys.executable).parent


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


#: El puerto local donde escucha el túnel SSM a la base.
DEFAULT_DB_PORT = "8100"

#: El puerto MySQL de una conexión directa, sin túnel.
DEFAULT_MYSQL_PORT = "3306"

#: El puerto de Aurora que reenvía el túnel SSM.
DEFAULT_AWS_PORT = "53360"


def aws_port_of(cfg) -> int:
    """El puerto de Aurora que reenvía el túnel SSM, para una config.

    La constante vive acá y no repetida en los ocho lugares que la usaban: un
    literal copiado se desincroniza sin que nadie lo note, y el default
    duplicado en la library y en el CLI son dos números que van a divergir.
    """
    return int(cfg.get("AWS_PORT", DEFAULT_AWS_PORT))


def mysql_port_of(cfg) -> int:
    """El puerto MySQL de la conexión directa, sin túnel SSM.

    ``DB_PORT`` es el puerto **local** del túnel SSM, que es otra cosa. Los dos
    usaban la misma clave con defaults distintos —8100 y 3306—, y sólo
    funcionaba porque las dos rutas son excluyentes: una o la otra. El
    template declaraba las dos en el mismo archivo, con la misma clave.

    La directa usa ``DB_MYSQL_PORT``. ``DB_PORT`` se sigue aceptando, porque
    un environment que ya lo tenía cargado no puede dejar de funcionar.
    """
    raw = cfg.get("DB_MYSQL_PORT") or cfg.get("DB_PORT") or DEFAULT_MYSQL_PORT
    return int(raw)


class Config:
    _config_dir: Path | None = None

    @classmethod
    def _from_values(cls, values: dict[str, str]) -> Config:
        """An *env-less* `Config` with values already in hand, no disk read.

        Exists for the workflow screen that asked for no environment at all —
        Kafka is full local — and has to answer that on every tick of a stream
        without re-reading `env.base` each time.

        `_env` queda en `None`, no en `""`: una config sin ambiente no se
        identifica con una cadena vacía, y hacerlo la volvería distinguible de
        `Config()` — que es exactamente el objeto que hereda la máquina del
        developer.
        """
        cfg = cls.__new__(cls)
        cfg._env = None
        cfg._values = values
        return cfg

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
        #
        # El valor del archivo se mira con `is not None` y no con
        # `key in self._values`: una clave escrita **sin valor** —`AWS_PROFILE=`
        # sin nada atrás— vale `None` en el dotenv, y con la otra forma el
        # `get` devolvía `None` sin llegar a los overrides. O sea, `require()`
        # fallaba con "Missing required config" aunque la variable estuviera
        # seteada en el entorno, que es justo lo que el override existe para.
        value = self._values.get(key)
        if value is not None:
            return value
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

        **Two keys say it, because two have said it over time.** `DB_MODE=local`
        is the explicit one. `AWS_ENVIRONMENT=local` is what
        `backend/config/env.environment.example` documented as "use the machine's
        native MySQL", and a real `env.local` declared that and nothing else.
        Reading only `DB_MODE` made that file behave as an AWS environment — it
        warmed AWS clients, offered the Aurora row and reached `AWS_HOST` at click
        time. Accepting both is what makes the template true again.
        """
        return any(
            (self.get(key) or "").strip().lower() == "local"
            for key in ("DB_MODE", "AWS_ENVIRONMENT")
        )

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
        """El puerto **local** donde escucha el túnel SSM a la base."""
        return int(self.get("DB_PORT", DEFAULT_DB_PORT))

    @property
    def aws_port(self) -> int:
        """El puerto de Aurora que reenvía el túnel SSM."""
        return aws_port_of(self)

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
