"""Leer de un socket, en el único lugar donde se hace.

Había dos copias de `_recv_exactly`, byte a byte salvo el docstring: una en
`adapters/database/connection.py` para el saludo de MySQL, y otra en
`application/workflow/readiness.py` para el probe de Kafka. Idénticas, y con
la misma dificultad de cambiar las dos sin notar que la otra quedó atrás.
"""

from __future__ import annotations

import socket


def recv_exactly(sock: socket.socket, size: int) -> bytes:
    """Read up to ``size`` bytes, stopping early if the peer stops sending.

    "Up to" y no "exactly": si el peer cierra a mitad, lo que llegó es lo que
    hay, y el que llama decide si eso alcanza.
    """
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
