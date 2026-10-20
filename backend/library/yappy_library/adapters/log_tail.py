"""Reading a log by byte offset, for a screen that polls.

``yappy logs --follow`` already tails by offset: it remembers where it stopped
and only prints what got appended. That logic lived in a Typer verb, where a
browser could not reach it, and the screen needs the same thing with a different
destination — the difference is ``console.print`` versus "return the bytes".

So the arithmetic moves here and both callers use it. Two properties matter and
both are load-bearing for a one-second poll:

- **Offsets are byte offsets, not line counts.** Asking for the last 60 lines of
  a file that grew between two polls would re-read the whole file to count
  newlines. The caller keeps the offset and asks for "everything after this".
- **Truncation is handled.** ``tunnel_log_path`` opens with ``"w"``: a tunnel
  restarted between two polls makes the file *shorter*. An offset past the end
  would otherwise return nothing forever. An offset beyond the current size
  resets to zero, which re-reads the file once and then behaves.
"""

from __future__ import annotations

from pathlib import Path

#: Read at most this much per poll. A log that grew by more than this between
#: two ticks is being flooded; showing the tail of it is more useful than
#: streaming megabytes into a DOM node.
MAX_CHUNK_BYTES = 256 * 1024


def tail_start_offset(raw: bytes, lines: int) -> int:
    """Byte offset where the last ``lines`` lines begin (0 if fewer exist).

    A file with no trailing newline has one fewer ``\\n`` than it has lines, so
    counting newlines and stopping at ``lines`` of them returns ``lines + 1``.
    That is the behaviour this had before it moved here: ``yappy logs -n 10``
    on a file ending without a newline showed eleven lines. The newline count is
    therefore compared against ``lines - 1`` when the file does not end in one.
    """
    if lines <= 0:
        return len(raw)
    # A file ending in a newline has as many newlines as lines; one that does
    # not has exactly one fewer. Counting newlines and stopping at `lines` of
    # them therefore returns one line too many when there is no trailing
    # newline — `yappy logs -n 10` showed eleven lines on those files.
    wanted = lines if raw.endswith(b"\n") else lines - 1
    count = 0
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] == 0x0A:
            count += 1
            if count > wanted:
                return i + 1
    return 0


def read_from(path: Path, offset: int, max_bytes: int = MAX_CHUNK_BYTES) -> tuple[str, int]:
    """Return ``(text, new_offset)``: what was appended after ``offset``.

    Returns an empty string and an unchanged offset when there is nothing new,
    so a caller polling on a timer does no work and sends nothing.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return "", offset

    if offset > size:
        # The file shrank: it was truncated and restarted. Start over rather
        # than returning nothing forever.
        offset = 0

    if size == offset:
        return "", offset

    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(max_bytes)
    except OSError:
        return "", offset

    new_offset = offset + len(chunk)
    return chunk.decode("utf-8", errors="replace"), new_offset


def last_lines(path: Path, lines: int = 60, max_bytes: int = MAX_CHUNK_BYTES) -> str:
    """The last ``lines`` lines of a log file, oldest first.

    Reads at most ``max_bytes`` from the end of the file, not the whole file.
    The whole file was a bomb: a Kafka log of 2 GB meant 2 GB en memoria por
    conexión de stream abierta, y el camino incremental ya estaba capado —
    sólo la primera lectura no lo estaba.

    When the file is bigger than the cap, the chunk arranca en medio de una
    línea: esa primera línea parcial se descarta, porque no es una línea y
    mostrarla cortada confunde más de lo que ayuda.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            start = max(0, size - max_bytes)
            handle.seek(start)
            raw = handle.read(max_bytes)
    except OSError:
        return ""

    if start > 0:
        first_newline = raw.find(b"\n")
        if first_newline == -1:
            # Ninguna línea completa en el chunk: no hay cola que mostrar.
            return ""
        raw = raw[first_newline + 1:]

    offset = tail_start_offset(raw, lines)
    return raw[offset:].decode("utf-8", errors="replace")