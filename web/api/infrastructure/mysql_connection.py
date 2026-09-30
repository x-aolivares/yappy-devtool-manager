"""Establishing MySQL connections: CA resolution, RDS IAM tokens, error mapping.

Design constraints, all verified against the installed drivers:

* **IAM auth.** RDS IAM users authenticate with the `mysql_clear_password` plugin.
  PyMySQL implements it inline in `_process_auth` (the auth-switch path), so a
  plain `pymysql.connect(password=<token>)` works against RDS — no plugin hack
  needed. What PyMySQL does *not* do is verify TLS by default, hence the CA.
* **TLS is mandatory.** Even through the SSM tunnel (which lands on 127.0.0.1),
  RDS sees the bastion's IP and requires encryption. The token is a password;
  sending it in the clear would leak IAM access.
* **`die()` isolation.** `library/db/tunnel.py::_generate_token` calls `die()` →
  `sys.exit()`. Calling it from a request handler would kill the uvicorn worker,
  so the token is generated here with botocore directly and failures are raised
  as domain errors.
"""
from __future__ import annotations

import glob
import socket
import time
from pathlib import Path

import botocore.session
from botocore.exceptions import BotoCoreError, ClientError
from pymysql.connections import Connection
from pymysql.err import MySQLError, OperationalError

from library.config import Config

from ..domain.exceptions import (
    AwsCredentialsError,
    ConfigKeyMissingError,
    DbConnectionError,
)

#: `generate_db_auth_token` returns a token valid for 15 minutes.
TOKEN_TTL_SECONDS = 15 * 60
#: Regenerate a bit early so a token never dies mid-query.
TOKEN_REFRESH_MARGIN = 60


def resolve_ca_path(cfg: Config | None = None) -> str:
    """Locate the RDS CA bundle. Raises if it can't be found.

    Search order: explicit `RDS_CA_PATH` config, then `~/.aws/rds-ca-*.pem`.
    """
    explicit = cfg.get("RDS_CA_PATH") if cfg is not None else None
    if explicit:
        if not Path(explicit).exists():
            raise ConfigKeyMissingError("RDS_CA_PATH")
        return explicit

    candidates = sorted(glob.glob(str(Path.home() / ".aws" / "rds-ca-*.pem")))
    if not candidates:
        raise DbConnectionError(
            "RDS CA bundle not found. Expected ~/.aws/rds-ca-rsa2048-g1.pem.\n"
            "Download it from "
            "https://trust.amazon.com/ or set RDS_CA_PATH in config/env.base. "
            "TLS is required: without it the IAM token would be sent in clear."
        )
    return candidates[0]


class RdsTokenProvider:
    """Generates and caches RDS IAM auth tokens, one per environment.

    Tokens expire in 15 minutes and there is no CLI refresher for the web (the
    CLI's refresher owns `config/.env.local`, which the web must not write), so
    the web keeps its own short-lived in-memory cache and regenerates on demand.
    """

    def __init__(self, ttl: int = TOKEN_TTL_SECONDS, margin: int = TOKEN_REFRESH_MARGIN):
        self._ttl = ttl
        self._margin = margin
        self._cache: dict[str, tuple[str, float]] = {}

    def get(self, env: str) -> str:
        now = time.monotonic()
        cached = self._cache.get(env)
        if cached is not None:
            token, issued_at = cached
            if now - issued_at < self._ttl - self._margin:
                return token

        token = self._generate(env)
        self._cache[env] = (token, now)
        return token

    def invalidate(self, env: str) -> None:
        self._cache.pop(env, None)

    def _generate(self, env: str) -> str:
        known = Config.known_environments()
        if env not in known:
            from ..domain.exceptions import EnvironmentNotFoundError

            raise EnvironmentNotFoundError(env, known)
        cfg = Config.with_env(env)

        for key in ("AWS_HOST", "AWS_USER"):
            if not cfg.get(key):
                raise ConfigKeyMissingError(key, env)

        host = cfg.get("AWS_HOST")
        user = cfg.get("AWS_USER")
        # The token is signed for the *RDS auth port* (AWS_PORT, usually 53360),
        # not for the local tunnel port.
        port = int(cfg.get("AWS_PORT", "53360"))

        try:
            session = botocore.session.Session(profile=cfg.profile)
            rds = session.create_client("rds", region_name=cfg.region)
            token = rds.generate_db_auth_token(
                DBHostname=host, Port=port, DBUsername=user, Region=cfg.region
            )
        except ClientError as e:
            raise AwsCredentialsError(
                f"Could not generate an RDS token for '{env}' "
                f"(profile '{cfg.profile}'): {e}"
            ) from e
        except BotoCoreError as e:
            raise AwsCredentialsError(
                f"Could not reach RDS to mint a token for '{env}': {e}. "
                f"Run 'yappy login aws' if your session expired."
            ) from e
        except Exception as e:
            raise AwsCredentialsError(
                f"Could not generate an RDS token for '{env}': {e}"
            ) from e

        if not token:
            raise DbConnectionError(f"RDS returned an empty auth token for '{env}'")
        return token


# --- connection specs ----------------------------------------------------


class EnvironmentConnection:
    """Connection to an environment's Aurora through the local SSM tunnel.

    The tunnel itself is owned by the CLI (`yappy run db <env> -d`); the web only
    connects to whatever is listening on DB_PORT. That keeps the web from ever
    touching library's shared process tracker.
    """

    requires_tls = True

    def __init__(self, env: str, token_provider: RdsTokenProvider):
        self.env = env
        self._tokens = token_provider

    @property
    def label(self) -> str:
        return f"env:{self.env}"

    def host(self) -> str:
        return "127.0.0.1"

    def port(self) -> int:
        return self._db_port()

    def user(self) -> str:
        # Not `Config.require()`: that raises a bare ValueError, and this layer
        # speaks domain errors. The explicit check gives the actionable message.
        user = self._cfg().get("AWS_USER")
        if not user:
            raise ConfigKeyMissingError("AWS_USER", self.env)
        return user

    def password(self) -> str:
        return self._tokens.get(self.env)

    def database(self) -> str | None:
        return None

    def _cfg(self) -> Config:
        return Config.with_env(self.env)

    def _db_port(self) -> int:
        return int(self._cfg().get("DB_PORT", "8100"))

    def require_tls(self) -> bool:
        # Always: this is RDS behind an SSM tunnel, and the tunnel's local port
        # has nothing to do with whether the remote end accepts cleartext.
        return True


class LocalConnection:
    """Connection to the developer's own MySQL server (the migration target)."""

    requires_tls = False

    def __init__(self, cfg: Config | None = None):
        self._cfg_override = cfg

    @property
    def label(self) -> str:
        return "local"

    def _cfg(self) -> Config:
        return self._cfg_override if self._cfg_override is not None else Config()

    def host(self) -> str:
        return self._cfg().get("LOCAL_DB_HOST", "127.0.0.1")

    def port(self) -> int:
        return int(self._cfg().get("LOCAL_DB_PORT", "3306"))

    def user(self) -> str:
        user = self._cfg().get("LOCAL_DB_USER", "root")
        if not user:
            raise ConfigKeyMissingError("LOCAL_DB_USER")
        return user

    def password(self) -> str:
        return self._cfg().get("LOCAL_DB_PASSWORD", "") or ""

    def database(self) -> str | None:
        return self._cfg().get("LOCAL_DB_NAME") or None

    def require_tls(self) -> bool:
        return str(self._cfg().get("LOCAL_DB_SSL", "false")).lower() in {
            "1",
            "true",
            "yes",
        }


# --- connecting ----------------------------------------------------------


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, str(e)


def connect(spec, connect_timeout: int = 10) -> Connection:
    """Open a MySQL connection, translating every failure into a domain error."""
    kwargs: dict = {
        "host": spec.host(),
        "port": spec.port(),
        "user": spec.user(),
        "password": spec.password(),
        "connect_timeout": connect_timeout,
        "read_timeout": 60,
        "write_timeout": 60,
        # Multi-statement stays OFF. It's the second half of the read-only
        # guarantee; `sql_guard` is the first.
        "client_flag": 0,
    }
    database = spec.database()
    if database:
        kwargs["database"] = database

    if spec.requires_tls and spec.require_tls():
        # Probe before resolving the CA. A closed tunnel port is by far the most
        # common failure, and "run yappy run db <env> -d" is a far more useful
        # message than "no CA bundle found" — which would otherwise mask it.
        reachable, detail = tcp_probe(spec.host(), spec.port(), timeout=2.0)
        if not reachable:
            raise DbConnectionError(_closed_port_message(spec, detail))
        kwargs["ssl_ca"] = resolve_ca_path()
        kwargs["ssl_verify_cert"] = True

    try:
        return Connection(**kwargs)
    except OperationalError as e:
        raise DbConnectionError(_explain_connection_error(spec, e)) from e
    except MySQLError as e:
        raise DbConnectionError(f"MySQL error connecting to {spec.label}: {e}") from e
    except (ConfigKeyMissingError, DbConnectionError):
        raise
    except Exception as e:
        raise DbConnectionError(
            f"Unexpected error connecting to {spec.label}: {e}"
        ) from e


def _closed_port_message(spec, detail: str) -> str:
    """The message for 'nothing is listening', which needs a specific fix."""
    if getattr(spec, "env", None):
        return (
            f"Cannot reach MySQL at {spec.host()}:{spec.port()} for {spec.label} "
            f"({detail}). The tunnel is not open — start it first:\n"
            f"  yappy run db {spec.env} -d"
        )
    return (
        f"Cannot reach MySQL at {spec.host()}:{spec.port()} ({detail}). "
        f"Start your local server — the web can do it if you set "
        f"LOCAL_MYSQL_START_CMD in config/env.base."
    )


def _explain_connection_error(spec, e: OperationalError) -> str:
    """Turn PyMySQL's terse errno into something actionable."""
    args = e.args
    code = args[0] if args else None
    detail = args[1] if len(args) > 1 else str(e)

    if code == 2003:  # can't connect
        return _closed_port_message(spec, detail)
    if code == 1045:  # access denied
        return (
            f"Access denied for '{spec.user()}' at {spec.label} ({detail}).\n"
            f"Check IAM permissions, and that the RDS token has not expired."
        )
    if code in (1044, 1045, 1049):  # unknown database / access
        return f"{spec.label}: {detail}"
    return f"Could not connect to {spec.label} at {spec.host()}:{spec.port()}: {detail}"
