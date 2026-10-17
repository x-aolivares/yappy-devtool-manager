"""Reading a Kafka log well enough to say what failed, and what would fix it.

Two jobs, and both come from the same place: **a Java stack trace is not a
message.** The supervisor used to quote the last three non-empty lines of a failed
process's log, and for a broker that died on a locked file that is

    at java.base/sun.nio.fs.WindowsFileSystemProvider.implDelete(...)
    at java.base/sun.nio.fs.AbstractFileSystemProvider.deleteIfExists(...)
    at java.base/sun.nio.fs.Files.delete(...)

Three frames of a stack trace, which say where some file was deleted and not one
thing about why. The line that says why is twenty lines earlier and nobody reads
twenty lines. So this module pulls out the exception lines and drops the frames,
and everything downstream — the verdict the developer reads, and the decision
about whether to offer the clean button — works from that excerpt.

**The cleanable failure is specific and it has a cause.** It is not "Kafka did
not start": it is a ``log.dirs`` entry that survived a previous run because the
JVM still held a checkpoint open, which Windows refuses to delete. The broker then
replays that metadata log and dies with ``AccessDeniedException`` on it. Wiping
the storage and re-formatting is not a workaround, it is the fix — and it is the
only fix, which is why the web should be able to offer it instead of sending the
developer to a terminal.

What it is **not**: ``DuplicateBrokerRegistrationException``, which shows up in
``controller.log`` after an unclean restart and says, in Kafka's own words, "this
should self-resolve once the heartbeat manager expires the broker's session". A
button that appeared on that error would appear on almost every restart and fix
nothing. This module does not match it.
"""

from __future__ import annotations

import re
from pathlib import Path

#: How much of a log to read.
#:
#: The whole file, not a tail, and the reason is measured: a broker log on this
#: project is 3.9 MB / 54.000 líneas and reads in 12ms. A tail is the wrong shape
#: for the question being asked — **a log file is reused across runs** (Windows
#: recycles the pid, so ``kafka-server-<pid>.log`` picked up a later, successful
#: start two days after the failed one), and the failure that explains this start
#: can be thousands of lines back. Scanned once, by a thread whose job is to wait
#: anyway, and only when a start has already failed.
READ_LIMIT_BYTES = 16 * 1024 * 1024

#: The fix we know how to apply. A name and not a sentence: it travels to the
#: browser as a field the row renders, and the wording belongs where the button is.
FIX_CLEAN_STORAGE = "kafka_clean_storage"

#: A line of a Java stack trace. Pure noise for this purpose, and there can be
#: dozens of them between two lines that actually say something.
_FRAME = "\tat "

#: The level in a Kafka log line.
#:
#: Not ``line.startswith("ERROR")``: Kafka writes ``[2026-10-14 14:29:19,023] ERROR
#: [SharedServer id=1] ...``, so the level sits *after* the timestamp and a prefix
#: test never matches a single one of them.
_LEVEL = re.compile(r"\]\s+(ERROR|FATAL|WARN)\b")


def failure_excerpt(text: str | list[str], limit: int = 3) -> list[str]:
    """The lines of ``text`` that say what failed, with the stack frames dropped.

    What the developer reads instead of three frames of ``Files.delete``:

        ERROR [SharedServer id=1] Got exception while starting SharedServer
        java.nio.file.AccessDeniedException: C:\\tmp\\kraft-combined-logs\\...

    The ``ERROR`` line says *when* — while starting — and the exception line says
    *what*. Either one alone is half an answer, so they travel together.

    Deduplicated, because a broker that retries its metadata log writes the same
    exception fifteen times and fifteen copies of the same line is a worse message
    than two different ones.
    """
    raw = text.splitlines() if isinstance(text, str) else list(text)
    lines = [line.strip() for line in raw if line.strip()]
    if not lines:
        return []

    exceptions = [i for i, line in enumerate(lines) if _is_exception_header(line)]
    if exceptions:
        # The **last** one, not the first: the log accumulates across runs (see
        # `read_log`), so the first exception in the file may be from a start that
        # happened days ago and succeeded afterwards. The one that explains the
        # failure being reported is the one nearest the end.
        last = exceptions[-1]
        chosen: list[str] = []
        for i in range(last - 1, -1, -1):
            if _is_error_line(lines[i]):
                chosen.append(lines[i])
                break
        chosen.append(lines[last])
        return _dedupe(chosen)[:limit]

    causes = [l for l in lines if l.startswith("Caused by:")]
    if causes:
        return _dedupe(causes)[:limit]

    errors = [l for l in lines if _is_error_line(l)]
    if errors:
        return _dedupe(errors)[:limit]

    quiet = [l for l in lines if not l.startswith(_FRAME)]
    return _dedupe(quiet)[-limit:] or lines[-limit:]


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            kept.append(line)
    return kept


def _is_exception_header(line: str) -> bool:
    """Whether this line names an exception and, usually, what it was about.

    A line like ``java.nio.file.AccessDeniedException: C:\\tmp\\kraft-combined-logs``
    matches on the dotted type followed by a capitalized word. A stack frame cannot
    match because the frames were already dropped, and a plain log message does not
    look like a package name.
    """
    if line.startswith("Caused by:") or line.startswith(_FRAME):
        return False
    head = line.split(":", 1)[0]
    if "." not in head or not head[0].islower():
        return False
    last = head.rsplit(".", 1)[-1]
    return last.endswith(("Exception", "Error")) and last[:1].isupper()


def _is_error_line(line: str) -> bool:
    """Whether this line carries an ``ERROR`` or ``FATAL`` level.

    Level and not message: a line can *talk about* errors without being one, and
    ``Caused by: org.apache...Error`` is the exception line, not the context line.
    """
    return bool(_LEVEL.search(line))


def needs_clean(text: str | list[str], dirs: list[str]) -> bool:
    """Whether this failure is the one that wiping the Kafka storage fixes.

    The test is deliberately narrow — an exception line, of the right kind, naming a
    file inside a configured ``log.dirs`` — because the cost of getting it wrong is
    asymmetric. Offer the button on something else and the developer clicks it,
    loses their topics, and the broker still does not start. Miss it and they do the
    one thing that works, from a terminal, as they do today.
    """
    if not dirs:
        return False
    needles = [_normalised(d) for d in dirs if d]
    for line in failure_excerpt(text):
        if "AccessDeniedException" not in line:
            continue
        haystack = _normalised(line)
        if any(needle in haystack for needle in needles):
            return True
    return False


def _normalised(path: str) -> str:
    """A path reduced to what two sources can be compared on: case and separators.

    ``log.dirs=/tmp/kraft-combined-logs`` in the config and
    ``C:\\tmp\\kraft-combined-logs\\...`` in the JVM's message are the same directory
    written by two programs with two conventions. Compared raw they never match, and
    the button silently never appears — which is the worst failure mode available to
    this feature, because it looks exactly like "nothing is broken".
    """
    return path.strip().replace("\\", "/").rstrip("/").lower()


def read_log(path: Path) -> list[str]:
    """A log file, decoded leniently, best-effort.

    A log is whatever a third-party process felt like writing, so it can hold
    anything. A missing file, an unreadable one, or a binary-ish tail returns an
    empty list rather than raising: the caller is a supervisor deciding what to put
    on a screen, and it must not die of a log it cannot read.

    Reads at most :data:`READ_LIMIT_BYTES` from the **end** of the file, which is
    where the current run is. A log that grew past the cap is truncated rather than
    loaded whole: the failure being looked for appears early in a run that died.
    """
    try:
        with Path(path).open("rb") as handle:
            try:
                handle.seek(-READ_LIMIT_BYTES, 2)
            except OSError:
                handle.seek(0)
            raw = handle.read()
    except (OSError, ValueError):
        return []
    return raw.decode("utf-8", errors="replace").splitlines()


def log_dirs(kafka_core: Path) -> list[str]:
    """The ``log.dirs`` of the broker, in the form an error message prints them.

    Kafka resolves a relative ``log.dirs`` against its own directory and the JVM
    prints the resolved path, so the comparison has to be on the resolved form or
    nothing ever matches. Lives here and not in the service because both need it:
    the service to wipe them, this to know whether an error is about them.
    """
    props = Path(kafka_core) / "config" / "kraft" / "server.properties"
    if not props.exists():
        return []
    dirs: list[str] = []
    for line in props.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("log.dirs="):
            continue
        for entry in line.split("=", 1)[1].split(","):
            entry = entry.strip()
            if not entry:
                continue
            path = Path(entry)
            dirs.append(str(path if path.is_absolute() else Path(kafka_core) / path))
    return dirs


def fix_for(log_file: str, dirs: list[str]) -> str:
    """The fix we know how to apply for this failure, or ``""``.

    Takes the log path rather than the process: the fix is a property of *what the
    log says*, and keeping the knowledge here means the supervisor can ask the
    question without knowing anything about Kafka's storage layout.
    """
    if not log_file or not dirs:
        return ""
    if needs_clean(read_log(Path(log_file)), dirs):
        return FIX_CLEAN_STORAGE
    return ""