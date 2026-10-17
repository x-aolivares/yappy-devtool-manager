"""Filesystem registry of the processes this tool started.

One JSON file per pid under ``~/.yappy/tracker/``. It is the only state that
survives the process that wrote it, which is what lets the web UI show a tunnel
someone opened from the terminal.

Two rules the rest of the codebase depends on:

- **Everything is optional on read.** The files outlive the code that wrote
  them, so a tracker directory can hold entries from a version that did not have
  half these fields. :func:`get_tracked_processes` fills the gaps in rather than
  skipping the entry, because an older entry is still a real running process.
  That includes ``exe``/``create_time``: an entry with neither is *unproven*, not
  suspect, and the kill path treats it as such. See
  :mod:`yappy_library.adapters.process_identity` for what those two fields buy.

- **``resource`` is the kind of thing, not an identifier.** ``"tunnel"``,
  ``"kafka"``, ``"service"``, ``"refresher"``. Which *port* a process serves
  lives in ``local_port``; it used to live nowhere, which made a tracked tunnel
  impossible to attribute to a row.

Liveness is deliberately **not** stored. It is read from the OS on every call,
because a process dying is the single thing this file exists to notice, and a
cached "alive" would hide exactly the event worth noticing.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from yappy_library.adapters import process_identity
from yappy_library.adapters.state_bus import state_bus

_TRACKER_DIR = Path.home() / ".yappy" / "tracker"

#: Who started the process. ``cli`` is the default so entries written before
#: this field existed read as foreign to the web UI, which is what they are:
#: the web did not start them.
OWNER_CLI = "cli"
OWNER_WEB = "web"

#: The states a *tracked* process is written in. This describes how the start
#: went, not whether the process is still there — that is ``alive``, read live.
#: "it died" is derived by the caller from ``alive`` being false.
STATE_STARTING = "starting"
STATE_UP = "up"
STATE_ERROR = "error"

#: ``name -> (mtime_ns, size, parsed)``. Parsing every file on every call is
#: fine for `yappy ps` and wasteful for a UI polling once a second, and the
#: parse is the expensive half: it is the only thing that touches the disk.
_parsed_cache: dict[str, tuple[int, int, dict]] = {}
_cache_lock = threading.Lock()


def _forget(name: str) -> None:
    with _cache_lock:
        _parsed_cache.pop(name, None)


def _parse(path: Path) -> dict | None:
    """Read one tracker file, reusing the last parse while the file is unchanged."""
    try:
        stat = path.stat()
    except OSError:
        return None

    with _cache_lock:
        cached = _parsed_cache.get(path.name)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    with _cache_lock:
        _parsed_cache[path.name] = (stat.st_mtime_ns, stat.st_size, data)
    return data


def track_process(
    pid: int,
    resource: str,
    target: str = "",
    env: str = "",
    log_file: str | None = None,
    *,
    local_port: int | None = None,
    remote_port: int | None = None,
    remote_host: str | None = None,
    cluster: str | None = None,
    label: str = "",
    owner: str = OWNER_CLI,
    state: str = STATE_UP,
    supervisor: int | None = None,
    exe: str | None = None,
    create_time: float | None = None,
):
    """Register ``pid`` so it can be found, attributed and stopped later.

    ``local_port`` is the field that makes a tracked tunnel meaningful to a UI:
    without it the registry can say "a tunnel is running" and nothing more.

    ``exe`` and ``create_time`` answer "is this still the process we started?",
    which a bare pid cannot. Windows hands the same pid to a new process within
    hours, and this file outlives the process it names — so an entry is a claim
    about a pid that has to be checked before anything kills it. Both default to
    what the OS says right now, because the caller that just spawned the process
    is the one who has it open and the one with the best chance of reading it.

    Passing either explicitly is for the cases where the live read is not the
    truth: a re-adopted pid, or a test. They are optional on read too, and that
    is load-bearing — see the module docstring.
    """
    if exe is None or create_time is None:
        live = process_identity.identity(pid)
        if live is not None:
            if exe is None:
                exe = live.exe
            if create_time is None:
                create_time = live.create_time or None

    _TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "pid": pid,
        "resource": resource,
        "target": target,
        "env": env,
        "started_at": time.time(),
        "log_file": log_file or "",
        "local_port": local_port,
        "remote_port": remote_port,
        "remote_host": remote_host or "",
        "cluster": cluster or "",
        "label": label,
        "owner": owner,
        "state": state,
        "supervisor": supervisor,
        "exe": exe or "",
        "create_time": create_time or 0.0,
    }
    path = _TRACKER_DIR / f"{pid}.json"
    path.write_text(json.dumps(entry, indent=2))
    _forget(path.name)
    state_bus.publish()


def update_process_state(pid: int, state: str, fix: str = "") -> None:
    """Move an already-tracked process to another start state.

    Only the state moves. Rewriting the whole entry here would race with a
    tracker written by a *different* process — a CLI tunnel and the web both
    touching the same file would each clobber the other's fields.

    This is the one write the whole push feature is built around: it is what the
    supervisor calls when a start finally has a verdict, so publishing here is what
    makes the row move without the screen ever asking.

    ``fix`` rides along for the same reason: it is the name of something that would
    make this start work — ``kafka_clean_storage`` and whatever comes next — and the
    screen needs it *with* the verdict, not one poll later. A name and not a
    sentence, because the wording belongs where the button is drawn.
    """
    path = _TRACKER_DIR / f"{pid}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    data["state"] = state
    if fix:
        data["fix"] = fix
    try:
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        return
    _forget(path.name)
    state_bus.publish()


def _process_alive(pid) -> bool:
    """Whether that pid still has a process behind it.

    Read through :mod:`yappy_library.adapters.process_identity`, not
    ``os.kill(pid, 0)``. On Windows the latter is not a liveness probe: CPython
    implements it as ``OpenProcess(PROCESS_ALL_ACCESS)``, which asks for *full
    control* of the target and fails — ``PermissionError`` — for anything the
    caller does not fully own. msys processes, elevated windows and other users'
    sessions therefore read as dead while being very much alive, and the whole UI
    quietly lies about them: a running tunnel shows as ``dead``, ``Parar todo``
    skips it, and the entry never goes away. ``PROCESS_QUERY_LIMITED_INFORMATION``
    asks only to look, which is all a liveness answer needs.
    """
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            return process_identity.identity(int(pid)) is not None
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        # POSIX: the process exists, we are just not allowed to signal it. That
        # is an *alive* answer, and treating it as dead is the same bug the
        # Windows branch above exists to avoid.
        return True
    except (OSError, ProcessLookupError, TypeError, ValueError):
        return False


def track_process_state_and_supervisor(pid: int, state: str, supervisor: int) -> None:
    """Move a tracked process to ``state`` and name who will resolve it.

    Same read-modify-write as :func:`update_process_state`, so a CLI tunnel and
    the web cannot clobber each other's fields.
    """
    path = _TRACKER_DIR / f"{pid}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    data["state"] = state
    data["supervisor"] = supervisor
    try:
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        return
    _forget(path.name)
    state_bus.publish()


def _mtime(path: Path) -> float:
    """Timestamp de escritura, para ordenar. 0 si el archivo ya no está."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def prune_dead_on_port(port: int) -> list[int]:
    """Forget the tracked processes on ``port`` that are no longer running.

    A process that falls over on its own leaves its entry behind forever — nobody
    untracks it, because `untrack_process` only runs on a clean stop. Without this
    the registry grows without bound and every `start` on that port adds another
    ghost on top of the previous one: `ensure_port_free` only looks at live
    entries, sees the port free, and starts another.

    Called right before a start, not during a snapshot, so that `dead` stays
    visible for as long as the developer is looking at it. The moment they ask
    for a new one on that port, the ghosts go.
    """
    forgotten = []
    for entry in get_tracked_processes():
        if entry.get("local_port") == port and not entry.get("alive"):
            pid = entry.get("pid")
            if pid:
                untrack_process(pid)
                forgotten.append(pid)
    return forgotten


def _matching(
    paths: list[Path],
    resource: str | None,
    target: str | None,
    env: str | None,
) -> list[dict]:
    """The parsed entries the filters keep, in the order they arrived.

    Separate from :func:`get_tracked_processes` because liveness is no longer
    knowable one entry at a time: on Windows answering it means taking one
    process-table snapshot for the whole batch, so the batch has to exist before
    the first question is asked.
    """
    kept: list[dict] = []
    for f in paths:
        parsed = _parse(f)
        if parsed is None:
            continue
        if resource and parsed.get("resource") != resource:
            continue
        if target and parsed.get("target") != target:
            continue
        if env and parsed.get("env") != env:
            continue
        kept.append(parsed)
    return kept


def _liveness_for(entries: list[dict]) -> dict[int, bool]:
    """``pid -> alive`` for every pid the entries mention, supervisors included.

    One bulk question first, and the per-pid path only when the bulk one could not
    answer — see :func:`process_identity.alive_map` for why "could not answer" is
    not "all dead".
    """
    pids: set[int] = set()
    for parsed in entries:
        for key in ("pid", "supervisor"):
            value = parsed.get(key)
            if value:
                pids.add(int(value))

    answers = process_identity.alive_map(pids)
    if answers is not None:
        return answers
    return {pid: _process_alive(pid) for pid in pids}


def get_tracked_processes(
    resource: str | None = None,
    target: str | None = None,
    env: str | None = None,
) -> list[dict]:
    """Every tracked process, newest first, with a live ``alive`` flag.

    Entries are always returned with every current key present, defaulted for
    files written by an older version. ``local_port`` stays ``None`` when the
    writer did not record one: that is not the same as port 0, and a caller
    showing a row per port needs to tell those apart.
    """
    results: list[dict] = []
    if not _TRACKER_DIR.exists():
        return results

    # "Newest first" por mtime, no por nombre de archivo. Ordenar por nombre es
    # ordenar pids **como texto**: "9999.json" queda antes que "33940.json", que
    # es al revés de lo que quiere decir. Con dos entradas del mismo puerto eso
    # hace que cuál de las dos sea "la dueña" dependa de la cantidad de dígitos
    # del pid, y el conflicto de una fila parpadea entre polls.
    paths = sorted(_TRACKER_DIR.glob("*.json"), key=_mtime, reverse=True)
    seen = {p.name for p in paths}
    with _cache_lock:
        for name in [n for n in _parsed_cache if n not in seen]:
            del _parsed_cache[name]

    candidates = _matching(paths, resource, target, env)
    alives = _liveness_for(candidates)

    for parsed in candidates:
        # A copy: the cached dict is shared between calls and callers annotate
        # what they get back.
        entry = dict(parsed)

        pid = entry.get("pid")
        entry["alive"] = alives.get(pid, False) if pid else False

        # The process that was going to resolve `starting` into `up` or `error`.
        # If it is gone, nobody ever will: a web server that shut down mid-start
        # leaves the entry behind, and without this the row says "levantando…"
        # forever even though the Kafka broker is happily serving.
        supervisor = entry.get("supervisor")
        if supervisor is not None:
            entry["supervisor_alive"] = alives.get(supervisor, False)

        # Files written before these fields existed. They predate the web UI
        # entirely, so `cli` is the honest owner and the process did start.
        entry.setdefault("owner", OWNER_CLI)
        entry.setdefault("state", STATE_UP)
        entry.setdefault("label", "")
        entry.setdefault("local_port", None)
        entry.setdefault("remote_port", None)
        entry.setdefault("remote_host", "")
        entry.setdefault("cluster", "")
        entry.setdefault("log_file", "")
        entry.setdefault("supervisor", None)
        entry.setdefault("supervisor_alive", True)
        # Written by a version that predates the identity fields. `""`/`0.0`,
        # not None, so a caller can tell "there was nothing to record" from
        # "the key is missing" — and both of those mean *do not refuse to stop*,
        # which is the only thing they are allowed to mean.
        entry.setdefault("exe", "")
        entry.setdefault("create_time", 0.0)
        # What would make a failed start work, when we know of something that
        # would. Empty is the honest answer for almost every failure.
        entry.setdefault("fix", "")

        results.append(entry)

    return results


def untrack_process(pid: int):
    f = _TRACKER_DIR / f"{pid}.json"
    if f.exists():
        f.unlink()
    _forget(f.name)
    state_bus.publish()