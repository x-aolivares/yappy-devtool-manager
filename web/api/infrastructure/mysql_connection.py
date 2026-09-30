"""Establishing MySQL connections: TLS setup, RDS IAM tokens, error mapping.

Design constraints, all verified against the installed drivers:

* **IAM auth.** RDS IAM users authenticate with the `mysql_clear_password` plugin.
  PyMySQL implements it inline in `_process_auth` (the auth-switch path), so a
  plain `pymysql.connect(password=<token>)` works against RDS — no plugin hack
  needed.
* **TLS is mandatory.** Even through the SSM tunnel (which lands on 127.0.0.1),
  RDS sees the bastion's IP and requires encryption. The token is a password;
  sending it in the clear would leak IAM access.
* **TLS must be REQUIRED, never PREFERRED.** PyMySQL's no-options path
  (`connections.py:301`) sets `self._ssl_required = False` and falls back to
  cleartext if the server declines SSL — which would leak the token on exactly
  the connection we are trying to protect. So an ssl dict is always passed, even
  when it carries no CA: a non-empty dict is what flips `_ssl_required = True`.
* **The CA is optional, the encryption is not.** Without a CA bundle we still
  encrypt, we just cannot verify the server's identity, so a MITM inside the VPC
  could impersonate RDS. That is a real but narrow risk (it requires an attacker
  already on the network path), and it is strictly better than the alternative:
  refusing to connect at all. `yappy web ca` fetches the bundle to close it.
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

#: Where `yappy web ca` puts the bundle.
CA_FILENAME = "rds-combined-ca-bundle.pem"
#: Official AWS download for the RDS root CAs (all regions in one file).
CA_URL = "https://s3.amazonaws.com/rds-downloads/rds-combined-ca-bundle.pem"


def ca_install_path() -> Path:
    return Path.home() / ".aws" / CA_FILENAME


def resolve_ca_path(cfg: Config | None = None) -> str | None:
    """Locate the RDS CA bundle, or None if there isn't one.

    Search order: explicit `RDS_CA_PATH` config, then the bundle installed by
    `yappy web ca`, then any per-region `rds-ca-*.pem` dropped in `~/.aws`.

    Returns None rather than raising: a missing CA degrades verification, it
    does not break the connection. An explicit `RDS_CA_PATH` that points nowhere
    *is* an error, because it means the config is wrong and silently falling back
    would hide it.

    `cfg=None` falls back to the base config rather than skipping the lookup.
    The connect path calls this with no argument, and reading `RDS_CA_PATH` only
    when a Config was injected would document a key that does nothing.
    """
    if cfg is None:
        cfg = Config()

    explicit = cfg.get("RDS_CA_PATH")
    if explicit:
        if not Path(explicit).exists():
            raise ConfigKeyMissingError("RDS_CA_PATH")
        return explicit

    installed = ca_install_path()
    if installed.exists():
        return str(installed)

    candidates = sorted(glob.glob(str(Path.home() / ".aws" / "rds-ca-*.pem")))
    return candidates[0] if candidates else None


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


def tls_options(cfg: Config | None = None) -> tuple[dict, bool]:
    """Build the PyMySQL kwargs for a **required** TLS session.

    Returns ``(kwargs, verified)``. ``verified`` is False when the channel is
    encrypted but the server's identity was not checked.

    Why the dict is built by hand instead of using ``ssl_ca``/``ssl_verify_cert``:
    both routes end at ``connections.py:301``, where ``if ssl:`` decides whether
    TLS is mandatory. Pass nothing and PyMySQL takes the ``elif SSL_ENABLED:``
    branch, which sets ``_ssl_required = False`` and **silently falls back to
    cleartext** if the server declines SSL. On a connection whose password is an
    RDS IAM token, that fallback is the exact leak we are guarding against, so
    the dict is always non-empty — it is the flag, not the content.

    ``check_hostname`` is always False. We connect to 127.0.0.1 (the SSM tunnel)
    while the certificate belongs to the RDS endpoint, so a hostname check would
    fail every time. Chain validation still happens via the CA.
    """
    ca = resolve_ca_path(cfg)
    if ca:
        return {"ssl": {"ca": ca, "check_hostname": False}}, True
    # No CA: still REQUIRED TLS, but the chain cannot be validated. "none" is a
    # non-empty dict, so `_ssl_required` stays True; `_create_ssl_ctx` maps it to
    # CERT_NONE while keeping ssl.create_default_context's sane floor (TLS 1.2+,
    # no legacy ciphers).
    return {"ssl": {"verify_mode": "none"}}, False


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
        # message than anything about certificates — which would otherwise mask it.
        reachable, detail = tcp_probe(spec.host(), spec.port(), timeout=2.0)
        if not reachable:
            raise DbConnectionError(_closed_port_message(spec, detail))
        kwargs.update(tls_options()[0])

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
