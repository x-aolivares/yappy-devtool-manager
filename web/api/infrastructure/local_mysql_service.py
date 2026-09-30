"""Lifecycle of the developer's own MySQL server (the migration target).

Why this exists: reaching a local MySQL from DBeaver/Workbench means running the
service by hand first, and the setup differs per machine. The web can do it, but
only if it stays configurable — the exact command is machine-specific (Windows
service, standalone `mysqld`, XAMPP), so it is read from config rather than
hardcoded:

    LOCAL_MYSQL_START_CMD   e.g. "net start MySQL80"  or  "C:/xampp/mysql/bin/mysqld.exe"
    LOCAL_MYSQL_STOP_CMD    optional
    LOCAL_DB_HOST / LOCAL_DB_PORT / LOCAL_DB_USER / LOCAL_DB_PASSWORD / LOCAL_DB_NAME

No command is invented here. With no `LOCAL_MYSQL_START_CMD` configured, `start`
raises with the config key to set, rather than guessing at a service name.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import time

from library.config import Config

from ..domain.entities import LocalMysqlStatus
from ..domain.exceptions import (
    ConfigKeyMissingError,
    LocalMysqlUnavailableError,
)
from .mysql_connection import LocalConnection, connect, tcp_probe

#: How long to wait for the server to accept connections after starting it.
STARTUP_TIMEOUT = 20.0
_POLL_INTERVAL = 0.5


class LocalMysqlService:
    """Implements LocalMysqlService. Stateless; the server is the state."""

    def __init__(self, cfg: Config | None = None):
        self._cfg = cfg

    def _config(self) -> Config:
        return self._cfg if self._cfg is not None else Config()

    # --- status ----------------------------------------------------------

    def status(self) -> LocalMysqlStatus:
        spec = LocalConnection(self._config())
        host, port = spec.host(), spec.port()
        running, detail = tcp_probe(host, port, timeout=1.5)
        return LocalMysqlStatus(
            running=running,
            host=host,
            port=port,
            user=_safe(spec.user, "?"),
            detail="" if running else detail,
            start_command=self._config().get("LOCAL_MYSQL_START_CMD", "") or "",
        )

    def is_running(self) -> bool:
        return self.status().running

    # --- start / stop ----------------------------------------------------

    def start(self) -> LocalMysqlStatus:
        current = self.status()
        if current.running:
            return current

        command = self._config().get("LOCAL_MYSQL_START_CMD", "") or ""
        if not command.strip():
            raise ConfigKeyMissingError("LOCAL_MYSQL_START_CMD")

        argv = _split_command(command)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                **(_no_window() if sys.platform == "win32" else {}),
            )
        except FileNotFoundError as e:
            raise LocalMysqlUnavailableError(
                f"Could not run LOCAL_MYSQL_START_CMD ('{argv[0]}'): {e}. "
                f"Check the path — on Windows a service needs 'net start <name>', "
                f"a bare mysqld needs its full path."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise LocalMysqlUnavailableError(
                f"LOCAL_MYSQL_START_CMD did not finish within 60s: '{command}'"
            ) from e

        if completed.returncode != 0:
            raise LocalMysqlUnavailableError(
                f"Start command failed (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout or '').strip()[:400]}"
            )

        waited = self._wait_until_up(current.host, current.port)
        if not waited:
            raise LocalMysqlUnavailableError(
                f"Command '{command}' ran but MySQL never accepted connections on "
                f"{current.host}:{current.port} after {STARTUP_TIMEOUT:.0f}s. "
                f"Check LOCAL_DB_HOST / LOCAL_DB_PORT."
            )
        return self.status()

    def stop(self) -> LocalMysqlStatus:
        command = self._config().get("LOCAL_MYSQL_STOP_CMD", "") or ""
        if not command.strip():
            raise ConfigKeyMissingError("LOCAL_MYSQL_STOP_CMD")

        try:
            completed = subprocess.run(
                _split_command(command),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                **(_no_window() if sys.platform == "win32" else {}),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise LocalMysqlUnavailableError(
                f"Could not run LOCAL_MYSQL_STOP_CMD: {e}"
            ) from e

        if completed.returncode != 0:
            raise LocalMysqlUnavailableError(
                f"Stop command failed (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout or '').strip()[:400]}"
            )

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            running, _ = tcp_probe(self.status().host, self.status().port, timeout=1.0)
            if not running:
                break
            time.sleep(_POLL_INTERVAL)
        return self.status()

    def verify_login(self) -> LocalMysqlStatus:
        """Confirm the server is up *and* the configured credentials work."""
        status = self.status()
        if not status.running:
            raise LocalMysqlUnavailableError(
                f"MySQL is not listening on {status.host}:{status.port}"
            )
        try:
            with connect(LocalConnection(self._config())) as conn:
                conn.ping(reconnect=False)
        except Exception as e:  # already a domain error; re-wrapped for clarity
            raise LocalMysqlUnavailableError(
                f"MySQL is listening on {status.host}:{status.port} but login "
                f"failed for '{status.user}': {e}. Check LOCAL_DB_USER / "
                f"LOCAL_DB_PASSWORD."
            ) from e
        return status

    # --- internals -------------------------------------------------------

    def _wait_until_up(self, host: str, port: int) -> bool:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            running, _ = tcp_probe(host, port, timeout=1.0)
            if running:
                return True
            time.sleep(_POLL_INTERVAL)
        return False


def _split_command(command: str) -> list[str]:
    """Split a config-provided command line, Windows-aware."""
    if sys.platform == "win32":
        # shlex's posix mode mangles backslashes in Windows paths
        return shlex.split(command, posix=False)
    return shlex.split(command)


def _no_window() -> dict:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - status must never raise
        return default
