"""Readiness probes: the part that decides whether "up" is a lie.

Each probe is exercised against a server that really speaks the protocol and
against one that only holds the port open, because the difference between those
two is the whole reason the probes exist.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest

from yappy_library.application.workflow.readiness import (
    CLUSTER_PROBE_PATH,
    probe_http,
    probe_kafka,
    probe_mysql,
    probe_for,
    wait_for,
)


@contextlib.contextmanager
def raw_server(handler):
    """Serve ``handler(conn)`` on a free port until the block exits."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    stop = threading.Event()

    def accept_loop():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            threading.Thread(target=handler, args=(conn,), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        sock.close()


@contextlib.contextmanager
def http_server(status: int = 200):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the name is the framework's
            body = self.path.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _respond(conn, payload: bytes) -> None:
    with conn:
        conn.sendall(payload)


@contextlib.contextmanager
def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    yield port


# --- http -------------------------------------------------------------------


def test_http_probe_accepts_any_answer():
    with http_server(status=404) as port:
        # 404 still means bytes moved through the tunnel, which is the claim.
        assert probe_http(port, path="/whatever") is True


def test_http_probe_reads_the_path():
    with http_server() as port:
        assert probe_http(port, path=CLUSTER_PROBE_PATH) is True


def test_http_probe_is_false_with_nothing_listening():
    with closed_port() as port:
        assert probe_http(port) is False


def test_http_probe_is_false_when_the_port_only_accepts():
    """The exact failure the probe exists for: bound, but nothing behind it."""
    with raw_server(lambda conn: None) as port:
        assert probe_http(port, timeout=0.5) is False


# --- mysql ------------------------------------------------------------------


def _greeting(protocol_version: int = 10) -> bytes:
    return (4).to_bytes(4, "little") + bytes([protocol_version])


def test_mysql_probe_accepts_a_greeting():
    with raw_server(lambda conn: _respond(conn, _greeting())) as port:
        assert probe_mysql(port) is True


def test_mysql_probe_rejects_another_protocol():
    with raw_server(lambda conn: _respond(conn, _greeting(protocol_version=9))) as port:
        assert probe_mysql(port) is False


def test_mysql_probe_is_false_when_the_port_only_accepts():
    with raw_server(lambda conn: None) as port:
        assert probe_mysql(port, timeout=0.5) is False


# --- kafka ------------------------------------------------------------------


def test_kafka_probe_asks_something_and_reads_the_reply():
    received = []

    def broker(conn):
        with conn:
            received.append(conn.recv(64))
            conn.sendall((8).to_bytes(4, "big"))

    with raw_server(broker) as port:
        assert probe_kafka(port) is True

    # A probe that never sent anything would pass against any socket that
    # writes four bytes for no reason.
    assert received and received[0]


def test_kafka_probe_is_false_when_the_port_only_accepts():
    with raw_server(lambda conn: None) as port:
        assert probe_kafka(port, timeout=0.5) is False


# --- wait_for ---------------------------------------------------------------


def test_wait_for_returns_as_soon_as_the_probe_answers():
    calls = []

    def probe():
        calls.append(1)
        return len(calls) >= 3

    assert wait_for(probe, timeout=5, interval=0.01) is True
    assert len(calls) == 3


def test_wait_for_gives_up_and_says_so():
    """A tunnel that never came up is an outcome to show, not to raise."""
    started = time.monotonic()

    assert wait_for(lambda: False, timeout=0.3, interval=0.05) is False
    assert time.monotonic() - started < 2


# --- resolution -------------------------------------------------------------


def test_probe_for_falls_back_instead_of_raising():
    # A resource that cannot name its own probe still gets a verdict; the wrong
    # guess shows as a row that never goes green, which is visible. A crash
    # shows as a screen that hangs.
    assert probe_for("something-new") is probe_http
    assert probe_for("kafka") is probe_kafka


def test_every_probe_takes_the_same_two_arguments():
    """A caller holding a kind name must not have to know the shape below it."""
    import inspect

    for kind, probe in probe_for.__globals__["PROBES"].items():
        params = list(inspect.signature(probe).parameters.values())
        assert [p.name for p in params[:2]] == ["port", "timeout"], kind


def test_the_cluster_probe_is_https():
    with raw_server(lambda conn: _respond(conn, b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")) as port:
        # Plain HTTP against a raw socket: the TLS handshake fails, which is the
        # correct answer for a port that is not serving TLS.
        assert probe_http(port, path=CLUSTER_PROBE_PATH, tls=True, timeout=0.5) is False


@pytest.mark.parametrize("kind", ["http", "https", "mysql", "kafka"])
def test_every_declared_probe_is_callable(kind):
    probe = probe_for(kind)
    with closed_port() as port:
        assert probe(port, 0.5) is False