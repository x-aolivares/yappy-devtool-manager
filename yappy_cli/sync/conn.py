"""Database connection per environment.

Replicates how yappy-cli-manager reaches Aurora:

1. ``DB_HOST`` + ``DB_USER`` -> direct TCP connection (reachable/local databases).
2. ``AWS_INSTANCE`` + ``AWS_HOST`` + ``AWS_PORT`` -> RDS auth token + SSM tunnel
   (same pattern as ``src/db/tunnel`` / ``src/base.ssm_tunnel``), connecting to
   ``localhost:<db_port>``.

``DB_USER`` / ``DB_PASSWORD`` fall back to ``config/.env.local`` (what
``yappy run db <env>`` writes).
"""

from __future__ import annotations

import contextlib
import socket
import time
from pathlib import Path
from typing import Iterator

import pymysql
from dotenv import dotenv_values

from ..base import BaseCommand
from ..config import Config
from ..db.tunnel import _generate_token


class SyncError(ValueError):
    """Raised when a target environment cannot be reached or inspected."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _local_env_values() -> dict[str, str]:
    path = _repo_root() / "config" / ".env.local"
    return dotenv_values(path) if path.exists() else {}


def _resolve_user(cfg: Config, local: dict[str, str]) -> str:
    return (
        cfg.get("DB_USER")
        or local.get("DB_USER")
        or cfg.aws_user
        or cfg.get("AWS_USER")
        or ""
    )


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise SyncError(f"SSM tunnel did not open on localhost:{port}")


def _open(host: str, port: int, user: str, password: str) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        connect_timeout=15,
        autocommit=True,
    )


@contextlib.contextmanager
def connect(cfg: Config) -> Iterator[pymysql.connections.Connection]:
    """Open a MySQL connection to the environment's database.

    The SSM tunnel is created on demand for the operation and closed at the end.
    """
    local = _local_env_values()
    user = _resolve_user(cfg, local)

    direct_host = cfg.get("DB_HOST")
    if direct_host:
        if not user:
            raise SyncError(
                "DB_USER is required for direct connections "
                "(DB_HOST is set but no user is configured)."
            )
        password = cfg.get("DB_PASSWORD") or local.get("DB_PASSWORD") or ""
        conn = _open(direct_host, int(cfg.get("DB_PORT", "3306")), user, password)
        try:
            yield conn
        finally:
            conn.close()
        return

    instance = cfg.get("AWS_INSTANCE")
    remote_host = cfg.get("AWS_HOST")
    remote_port = cfg.get("AWS_PORT")
    required = {
        "AWS_INSTANCE": instance,
        "AWS_HOST": remote_host,
        "AWS_PORT": remote_port,
        "AWS_REGION": cfg.get("AWS_REGION"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing or not user:
        if not user:
            missing.append("AWS_USER/_DB_USER")
        raise SyncError(
            "No reachable database for this environment. Configure either:\n"
            "  - Direct: DB_HOST + DB_USER (+ DB_PASSWORD)\n"
            "  - Tunnel (real AWS): AWS_INSTANCE + AWS_HOST + AWS_PORT + AWS_USER\n"
            f"Missing config: {', '.join(missing)}"
        )

    try:
        token = _generate_token(cfg)
    except SystemExit as exc:
        raise SyncError(f"Could not generate RDS auth token: {exc}") from exc

    base = BaseCommand()
    local_port = cfg.db_port
    proc = None
    try:
        proc = base.ssm_tunnel(
            instance=instance,
            port=int(remote_port),
            local_port=local_port,
            region=cfg.region,
            profile=cfg.profile,
            remote_host=remote_host,
            quiet=True,
        )
        _wait_for_port(local_port)
        conn = _open("127.0.0.1", local_port, user, token)
        try:
            yield conn
        finally:
            conn.close()
    finally:
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.terminate()
            base.kill_ssm(proc.pid)