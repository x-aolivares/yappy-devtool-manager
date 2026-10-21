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
import threading
import time
from typing import Iterator

import pymysql
from dotenv import dotenv_values

from yappy_library.adapters.database.credentials import generate_token, resolve_db_user
from yappy_library.adapters.processes import BaseCommand, ToolUnavailable, tunnel_log_path
from yappy_library.config import Config, mysql_port_of
from yappy_library.paths import project_config_dir


class SyncError(ValueError):
    """Raised when a target environment cannot be reached or inspected."""


def _local_env_values() -> dict[str, str]:
    """Los valores de `.env.local`, sin los que no tienen valor.

    El dotenv devuelve `None` para una clave escrita a secas, y un `None` en
    un valor de config es una sorpresa esperando a pasar algo.
    """
    path = project_config_dir() / ".env.local"
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _resolve_user(cfg: Config, local: dict[str, str]) -> str:
    return resolve_db_user(cfg, local)


#: Un lock por puerto local, para el check-and-spawn del túnel.
#:
#: Vive en el proceso y no en un archivo: el lock de archivo tendría que
#: estar tomado durante la espera del túnel, que puede ser de treinta
#: segundos, y eso colgaría al otro proceso entero. El caso que esto
#: resuelve es el real: dos pestañas del browser, o dos requests de la web,
#: clicks a la vez.
_port_locks: dict[int, threading.Lock] = {}
_port_locks_guard = threading.Lock()


def _port_lock(port: int) -> threading.Lock:
    """El lock de un puerto local, creado la primera vez que se usa."""
    with _port_locks_guard:
        lock = _port_locks.get(port)
        if lock is None:
            lock = threading.Lock()
            _port_locks[port] = lock
        return lock


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


def mysql_greeting(port: int, timeout: float) -> bool:
    """True when ``port`` answers with a MySQL server greeting.

    A successful TCP connect is *not* proof that a tunnel works:
    ``session-manager-plugin`` binds the local port as soon as the session
    starts, and when the remote leg is dead — the instance cannot reach the
    target host, or the data channel dropped — it keeps accepting connections
    that never produce a single byte, nor a FIN. Waiting on the greeting is
    what separates "the tunnel is up" from "the port is merely listening", and
    it is the difference between an error and an indefinite hang.

    Public because the Workflow screens run the same probe to decide
    whether a tunnel they just opened is actually up.
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
        if mysql_greeting(port, timeout=min(5.0, max(0.5, remaining))):
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
        conn = _open(direct_host, mysql_port_of(cfg), user, password)
        try:
            yield conn
        finally:
            conn.close()
        return

    instance = cfg.get("AWS_INSTANCE") or ""
    remote_host = cfg.get("AWS_HOST")
    remote_port = cfg.get("AWS_PORT") or ""
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
    except SystemExit as exc:        raise SyncError(f"No se pudo generar el token de autenticación de RDS: {exc}") from exc

    base = BaseCommand()
    local_port = cfg.db_port
    proc = None
    try:
        # Reuse before spawning. A second `start-session` on a local port that is
        # already forwarded cannot bind, so spawning one regardless leaked a
        # process per request — one per click, none of them ever cleaned up,
        # since the cleanup only runs when the request finishes. It also hid the
        # failure, because the tunnel is started quietly.
        #
        # Y el chequeo y el spawn tienen que ser **una** operación: dos
        # requests concurrent veían los dos "no hay túnel" y spawneaban los
        # dos. El segundo no puede tomar el puerto y falla en silencio —la
        # CLI escribe el error al log del túnel, que nadie mira—, y el
        # request se queda esperando un MySQL que no va a contestar.
        with _port_lock(local_port):
            if not mysql_greeting(local_port, timeout=1.0):
                try:
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
                except ToolUnavailable as exc:
                    # Every DB route runs on a sync `def`, so a `SystemExit` escaping
                    # here does not produce a 500 — it takes uvicorn with it, silently,
                    # because `asyncio.Task.__step` re-raises `BaseException` and no
                    # `except Exception` in the stack can catch it. `SyncError` is
                    # what every one of those routes already knows how to render.
                    raise SyncError(f"No se pudo abrir el túnel a la base: {exc.reason}") from exc
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
