"""Database connection per environment.

Replicates how yappy-cli-manager reaches Aurora:

1. ``DB_HOST`` + ``DB_USER`` -> direct TCP connection (reachable/local databases).
2. ``AWS_INSTANCE`` + ``AWS_HOST`` + ``AWS_PORT`` -> RDS auth token + SSM tunnel
   (same pattern as ``src/db/tunnel`` / ``src/base.ssm_tunnel``), connecting to
   ``localhost:<db_port>``.

``DB_USER`` / ``DB_PASSWORD`` fall back to ``backend/config/.env.local`` (what
``yappy run db <env>`` writes).
"""

from __future__ import annotations

import contextlib
import socket
import time
from typing import Iterator

import pymysql
from dotenv import dotenv_values

from yappy_library.adapters.database.credentials import generate_token
from yappy_library.adapters.processes import BaseCommand
from yappy_library.config import Config
from yappy_library.paths import project_config_dir


class SyncError(ValueError):
    """Raised when a target environment cannot be reached or inspected."""


def _local_env_values() -> dict[str, str]:
    path = project_config_dir() / ".env.local"
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
    raise SyncError(f"El túnel SSM no se abrió en localhost:{port}")


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
                "DB_USER es obligatorio para conexiones directas "
                "(DB_HOST está configurado pero no hay usuario)."
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
            "No hay ninguna base de datos alcanzable para este ambiente. Configurá una de estas:\n"
            "  - Directa: DB_HOST + DB_USER (+ DB_PASSWORD)\n"
            "  - Túnel (AWS real): AWS_INSTANCE + AWS_HOST + AWS_PORT + AWS_USER\n"
            f"Falta configurar: {', '.join(missing)}"
        )

    try:
        token = generate_token(cfg)
    except SystemExit as exc:
        raise SyncError(f"No se pudo generar el token de autenticación de RDS: {exc}") from exc

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
