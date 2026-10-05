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
from yappy_library.adapters.processes import BaseCommand, tunnel_log_path
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


#: The MySQL handshake starts with a 4-byte little-endian payload length, a
#: 1-byte sequence id, and then the payload, whose first byte is the protocol
#: version. Reading those 5 bytes is enough to know that something on the other
#: end speaks MySQL.
_MYSQL_GREETING_HEADER = 5
_MYSQL_PROTOCOL_VERSION = 10

#: Upper bound on the MySQL protocol exchange, in seconds.
#:
#: ``connect_timeout`` alone is not a bound at all here: the TCP connect to
#: ``127.0.0.1`` succeeds in milliseconds whether or not the SSM data channel
#: behind it works, so what actually needs bounding is the *read* of the server
#: greeting. Generous on purpose — a statement that streams rows resets it — and
#: the interactive page caps itself lower with a server-side timeout.
READ_TIMEOUT = 120
WRITE_TIMEOUT = 120


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    """Read exactly ``size`` bytes, or fewer if the peer stops sending."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _mysql_greeting(port: int, timeout: float) -> bool:
    """True when ``port`` answers with a MySQL server greeting.

    A successful TCP connect is *not* proof that a tunnel works:
    ``session-manager-plugin`` binds the local port as soon as the session
    starts, and when the remote leg is dead — the instance cannot reach the
    target host, or the data channel dropped — it keeps accepting connections
    that never produce a single byte, nor a FIN. Waiting on the greeting is
    what separates "the tunnel is up" from "the port is merely listening", and
    it is the difference between an error and an indefinite hang.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            header = _recv_exactly(sock, _MYSQL_GREETING_HEADER)
    except OSError:
        return False
    return len(header) == _MYSQL_GREETING_HEADER and header[4] == _MYSQL_PROTOCOL_VERSION


def _wait_for_port(port: int, timeout: float = 30.0) -> None:
    """Block until ``port`` answers with a MySQL greeting, or raise ``SyncError``."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _mysql_greeting(port, timeout=min(5.0, max(0.5, remaining))):
            return
        time.sleep(0.5)
    raise SyncError(
        f"El túnel SSM no respondió en localhost:{port}: el puerto está abierto pero no "
        f"llegó el saludo de MySQL. Suele significar que la instancia no puede alcanzar "
        f"el host remoto — revisá el security group que permite el puerto y que el "
        f"endpoint resuelva desde la instancia. El log del túnel está en "
        f"{tunnel_log_path(port)}"
    )


def _open(host: str, port: int, user: str, password: str) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        connect_timeout=15,
        read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT,
        autocommit=True,
    )


@contextlib.contextmanager
def connect(cfg: Config) -> Iterator[pymysql.connections.Connection]:
    """Open a MySQL connection to the environment's database.

    A healthy tunnel on the local port is reused when there already is one —
    either the one the operator opened by hand or one left behind by an earlier
    request. Otherwise the tunnel is created on demand for the operation and
    closed at the end.
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
        # Reuse before spawning. A second `start-session` on a local port that is
        # already forwarded cannot bind, so spawning one regardless leaked a
        # process per request — one per click, none of them ever cleaned up,
        # since the cleanup only runs when the request finishes. It also hid the
        # failure, because the tunnel is started quietly.
        if not _mysql_greeting(local_port, timeout=1.0):
            proc = base.ssm_tunnel(
                instance=instance,
                port=int(remote_port),
                local_port=local_port,
                region=cfg.region,
                profile=cfg.profile,
                remote_host=remote_host,
                quiet=True,
                log_file=tunnel_log_path(local_port),
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
