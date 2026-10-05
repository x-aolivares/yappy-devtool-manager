"""La conexión a la base no puede colgarse para siempre.

Contexto: la página "Ejecutar SQL" se quedó girando en "Consultando qa…" para
siempre, con cuatro túneles SSM colgados y ni un error en pantalla. La causa no
fue el SQL: el plugin de SSM abre el puerto local apenas arranca la sesión, así
que cuando la pata remota del túnel está muerta acepta conexiones TCP que nunca
devuelven ni un byte. `connect_timeout=15` no lo detecta porque solo limita el
*connect* —que en loopback tarda milisegundos—, y sin `read_timeout` la lectura
del saludo de MySQL queda bloqueada para siempre.

Estos tests fijan las tres propiedades que evitan la regresión:

1. `_mysql_greeting` distingue "el túnel habla MySQL" de "el puerto escucha".
2. `_wait_for_port` convierte un túnel muerto en un `SyncError` con mensaje.
3. `connect` reusa un túnel sano en vez de spawnear uno que no puede tomar el
   puerto local.
"""

import contextlib
import socket
import threading
import types

import pytest

from yappy_library.adapters.database import connection as conn_mod
from yappy_library.adapters.database.connection import SyncError, _mysql_greeting, _wait_for_port


def _greeting_bytes() -> bytes:
    """A MySQL server greeting header: length, sequence id, protocol version."""
    return b"\x4a\x00\x00\x00\x0a"


class _Listener:
    """A local TCP listener with a scripted answer for whoever connects."""

    def __init__(self, answer: bytes | None):
        self._answer = answer
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            # `answer=None` es el caso que hangueaba: se acepta la conexión y
            # no se manda absolutamente nada, sin MySQL y sin FIN.
            if self._answer is not None:
                with contextlib.suppress(OSError):
                    client.sendall(self._answer)
            with contextlib.suppress(OSError):
                client.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        with contextlib.suppress(OSError):
            self._sock.close()


@pytest.fixture
def listener():
    made: list[_Listener] = []

    def _make(answer: bytes | None) -> _Listener:
        srv = _Listener(answer)
        made.append(srv)
        return srv

    yield _make
    for srv in made:
        srv.close()


# --- _mysql_greeting --------------------------------------------------------


def test_a_mysql_greeting_is_recognized(listener):
    srv = listener(_greeting_bytes())
    assert _mysql_greeting(srv.port, timeout=2.0) is True


def test_a_port_that_answers_nothing_is_not_a_tunnel(listener):
    """El caso exacto del bug: el puerto acepta y no entrega ni un byte."""
    srv = listener(None)
    assert _mysql_greeting(srv.port, timeout=1.0) is False
    assert srv.connections == 1, "el probe tiene que intentar conectarse"


def test_a_port_that_answers_something_else_is_not_a_tunnel(listener):
    srv = listener(b"SSH-2.0-OpenSSH_8.7\r\n")
    assert _mysql_greeting(srv.port, timeout=2.0) is False


def test_a_closed_port_is_not_a_tunnel():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    assert _mysql_greeting(dead_port, timeout=1.0) is False


# --- _wait_for_port ---------------------------------------------------------


def test_wait_retries_until_the_tunnel_answers(monkeypatch):
    """El plugin tarda unos segundos en levantar el listener; no se rinde al primer
    intento, y en cuanto llega el saludo sigue."""
    attempts = {"n": 0}

    def _ready_on_third_probe(port, timeout):
        attempts["n"] += 1
        return attempts["n"] >= 3

    monkeypatch.setattr(conn_mod, "_mysql_greeting", _ready_on_third_probe)
    monkeypatch.setattr(conn_mod.time, "sleep", lambda _s: None)

    _wait_for_port(8101, timeout=5.0)
    assert attempts["n"] == 3


def test_a_tunnel_that_never_answers_raises_instead_of_hanging(listener, monkeypatch):
    srv = listener(None)
    monkeypatch.setattr(conn_mod.time, "sleep", lambda _s: None)

    with pytest.raises(SyncError) as exc:
        _wait_for_port(srv.port, timeout=1.0)

    message = str(exc.value)
    assert "no respondió" in message
    # El mensaje tiene que decir dónde mirar: sin esto el error es indistinguible
    # de un SELECT lento.
    assert "tunnel-" in message and ".log" in message


# --- _open ------------------------------------------------------------------


def test_open_bounds_the_mysql_protocol_not_only_the_tcp_connect(monkeypatch):
    """`connect_timeout` solo cubre el connect; el read es el que cuelga."""
    captured = {}

    def _fake_connect(**kwargs):
        captured.update(kwargs)
        return "conn"

    monkeypatch.setattr(conn_mod.pymysql, "connect", _fake_connect)

    assert conn_mod._open("127.0.0.1", 8101, "user", "token") == "conn"
    assert captured["connect_timeout"] == 15
    assert captured["read_timeout"] == conn_mod.READ_TIMEOUT
    assert captured["write_timeout"] == conn_mod.WRITE_TIMEOUT


# --- connect: reuso de túnel ------------------------------------------------


def _tunnel_cfg(port: int) -> types.SimpleNamespace:
    values = {
        "AWS_INSTANCE": "i-123",
        "AWS_HOST": "db.example.com",
        "AWS_PORT": "53360",
        "AWS_REGION": "us-east-1",
        "AWS_USER": "app",
    }
    return types.SimpleNamespace(
        db_port=port,
        region="us-east-1",
        profile="base-profile",
        aws_user="app",
        get=lambda key, default=None: values.get(key, default),
    )


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_connect_deps(monkeypatch, *, greeting: bool, spawns: list) -> None:
    monkeypatch.setattr(conn_mod, "_local_env_values", lambda: {})
    monkeypatch.setattr(conn_mod, "generate_token", lambda cfg: "token")
    monkeypatch.setattr(conn_mod, "_open", lambda *a, **kw: _FakeConn())
    monkeypatch.setattr(conn_mod, "_mysql_greeting", lambda port, timeout: greeting)
    monkeypatch.setattr(conn_mod, "_wait_for_port", lambda port, timeout=30.0: None)

    class _Base:
        def ssm_tunnel(self, **kwargs):
            spawns.append(kwargs)
            return types.SimpleNamespace(pid=4242, terminate=lambda: None)

        def kill_ssm(self, pid=None):
            spawns.append({"killed": pid})

    monkeypatch.setattr(conn_mod, "BaseCommand", _Base)


def test_an_existing_healthy_tunnel_is_reused_instead_of_spawned(monkeypatch):
    """Un click por request, y ningún proceso colgado: el puerto local ya está tomado."""
    spawns: list = []
    _patch_connect_deps(monkeypatch, greeting=True, spawns=spawns)

    with conn_mod.connect(_tunnel_cfg(8101)) as conn:
        assert conn is not None

    assert spawns == [], "no hay que spawnear un túnel que ya está sano"


def test_a_missing_tunnel_is_still_spawned_and_waits_for(monkeypatch):
    spawns: list = []
    _patch_connect_deps(monkeypatch, greeting=False, spawns=spawns)

    with conn_mod.connect(_tunnel_cfg(8101)):
        pass

    assert [s for s in spawns if "instance" in s][0]["local_port"] == 8101
    assert {"killed": 4242} in spawns, "el túnel que uno abrió lo cierra al terminar"


def test_a_spawned_tunnel_is_logged_instead_of_going_to_devnull(monkeypatch):
    """Con DEVNULL, un túnel que no pudo tomar el puerto era indistinguible de uno
    que todavía estaba levantando."""
    spawns: list = []
    _patch_connect_deps(monkeypatch, greeting=False, spawns=spawns)

    with conn_mod.connect(_tunnel_cfg(8101)):
        pass

    spawned = [s for s in spawns if "instance" in s][0]
    assert spawned["quiet"] is True
    assert spawned["log_file"].name == "tunnel-8101.log"
