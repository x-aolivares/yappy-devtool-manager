"""Does a tunnel actually carry bytes?

Every probe here answers the same question — *did something come back?* — and
none of them accept a successful TCP connect as the answer. That is the whole
reason this module exists:

``session-manager-plugin`` binds the local port the moment the session starts.
When the remote leg is dead — the instance cannot reach the target host, the
data channel dropped, the wrong security group — it keeps accepting connections
that never produce a byte nor a FIN. A probe that stops at "port is listening"
reports those as *up*, and the screen would then offer to open a URL that hangs.

So each probe reads. HTTP reads a response line, MySQL reads the protocol
greeting, Kafka reads the length prefix of a reply to a request it sent first.
A status code of 404 or 500 counts as up: what is being proven is that bytes
moved through the tunnel, not that the service likes the question.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from typing import Callable

from yappy_library.adapters.database.connection import mysql_greeting

#: Swagger UI is what the CLI already opens for a cluster tunnel, so asking for
#: it is asking for the thing the tunnel is for.
CLUSTER_PROBE_PATH = "/swagger-ui/index.html"

#: Kafka's ApiVersions request, key 18 at version 0. Sending a real request and
#: reading a real reply is what tells a broker apart from a socket that accepts.
_KAFKA_API_VERSIONS_KEY = 18

#: Generous enough for a cold JVM and a slow SSM handshake, short enough that
#: the UI gets an answer while the developer is still watching. Compiling a
#: three-hundred-table schema is slow and normal; a tunnel that has not answered
#: in a minute was not going to.
DEFAULT_TIMEOUT = 30.0
POLL_INTERVAL = 0.5


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    """Read up to ``size`` bytes, stopping early if the peer stops sending."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def probe_http(port: int, path: str = "/", tls: bool = False, timeout: float = 5.0) -> bool:
    """True when ``port`` completes an HTTP exchange.

    The certificate is not verified, on purpose. A cluster tunnel terminates
    TLS for a remote host and is reached over ``localhost``, so the certificate
    will never match; rejecting it would report every healthy HTTPS tunnel as
    down. What is being asked is whether bytes flow, not who is on the other
    end of a connection nobody authenticated.
    """
    conn: http.client.HTTPConnection
    if tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout, context=context)
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path or "/")
        conn.getresponse()
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def probe_mysql(port: int, timeout: float = 5.0) -> bool:
    """True when ``port`` answers with a MySQL server greeting."""
    return mysql_greeting(port, timeout)


def _kafka_api_versions_request() -> bytes:
    """A minimal v0 ApiVersions request, the cheapest thing a broker must answer.

    The Kafka request envelope is, in this order::

        size(int32) | apiKey(int16) | apiVersion(int16) |
        correlationId(int32) | clientId(nullable string)

    The first version of this put ``correlationId`` where ``apiKey`` goes, so
    Kafka read the correlation id as the api key, the api key as the version, and
    then tried to read a 28784-byte client id out of a 4-byte socket. It answered
    with ``InvalidRequestException`` and closed — the probe read no bytes and
    called a perfectly healthy broker down.

    ``size`` covers everything after itself, and ``clientId`` is a nullable
    string: an int16 length then the bytes.
    """
    client_id = b"yappy"
    body = (
        _KAFKA_API_VERSIONS_KEY.to_bytes(2, "big")
        + (0).to_bytes(2, "big")                     # api version 0
        + (1).to_bytes(4, "big")                     # correlation id
        + len(client_id).to_bytes(2, "big")
        + client_id
    )
    return len(body).to_bytes(4, "big") + body


def probe_kafka(port: int, timeout: float = 5.0) -> bool:
    """True when ``port`` answers a Kafka request.

    Four bytes back is enough: that is the length prefix every Kafka response
    starts with, so reading it proves something on the other end parses Kafka
    protocol rather than merely holding the port open.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_kafka_api_versions_request())
            return len(_recv_exactly(sock, 4)) == 4
    except OSError:
        return False


#: Probe per resource kind. The registry names one of these per resource.
#: Every entry takes ``(port, timeout)``, and nothing else: a caller holding a
#: kind name should never have to know whether the probe under it wanted a path.
PROBES: dict[str, Callable[[int, float], bool]] = {
    "http": lambda port, timeout=5.0: probe_http(port, path="/", tls=False, timeout=timeout),
    "https": lambda port, timeout=5.0: probe_http(
        port, path=CLUSTER_PROBE_PATH, tls=True, timeout=timeout
    ),
    "mysql": probe_mysql,
    "kafka": probe_kafka,
}


def probe_for(kind: str) -> Callable[[int, float], bool]:
    """The probe for a kind, defaulting to plain HTTP.

    Unknown kinds fall back rather than raise: a resource that cannot name its
    own probe should still get a verdict, and a wrong guess shows up as a
    resource that never goes green, which is visible. A crash shows up as a
    screen that hangs.
    """
    return PROBES.get(kind, probe_http)


def wait_for(probe: Callable[[], bool], timeout: float = DEFAULT_TIMEOUT,
             interval: float = POLL_INTERVAL) -> bool:
    """Poll ``probe`` until it says yes or ``timeout`` runs out.

    Returns a bool instead of raising, because the caller needs to record the
    negative: a tunnel that never came up is an outcome to show, not an error to
    swallow.
    """
    deadline = time.monotonic() + timeout
    while True:
        if probe():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))