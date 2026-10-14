"""Filesystem registry of the processes this tool started.

One JSON file per pid under ``~/.yappy/tracker/``. It is the only state that
survives the process that wrote it, which is what lets the web UI show a tunnel
someone opened from the terminal.

Two rules the rest of the codebase depends on:

- **Everything is optional on read.** The files outlive the code that wrote
  them, so a tracker directory can hold entries from a version that did not have
  half these fields. :func:`get_tracked_processes` fills the gaps in rather than
  skipping the entry, because an older entry is still a real running process.

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
import threading
import time
from pathlib import Path

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
):
    """Register ``pid`` so it can be found, attributed and stopped later.

    ``local_port`` is the field that makes a tracked tunnel meaningful to a UI:
    without it the registry can say "a tunnel is running" and nothing more.
    """
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
    }
    path = _TRACKER_DIR / f"{pid}.json"
    path.write_text(json.dumps(entry, indent=2))
    _forget(path.name)


def update_process_state(pid: int, state: str) -> None:
    """Move an already-tracked process to another start state.

    Only the state moves. Rewriting the whole entry here would race with a
    tracker written by a *different* process — a CLI tunnel and the web both
    touching the same file would each clobber the other's fields.
    """
    path = _TRACKER_DIR / f"{pid}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    data["state"] = state
    try:
        path.write_text(json.dumps(data, indent=2))
    except OSError:
        return
    _forget(path.name)


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

    paths = sorted(_TRACKER_DIR.glob("*.json"), reverse=True)
    seen = {p.name for p in paths}
    with _cache_lock:
        for name in [n for n in _parsed_cache if n not in seen]:
            del _parsed_cache[name]

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

        # A copy: the cached dict is shared between calls and callers annotate
        # what they get back.
        entry = dict(parsed)

        pid = entry.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                entry["alive"] = True
            except (OSError, ProcessLookupError):
                entry["alive"] = False
        else:
            entry["alive"] = False

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

        results.append(entry)

    return results


def untrack_process(pid: int):
    f = _TRACKER_DIR / f"{pid}.json"
    if f.exists():
        f.unlink()
    _forget(f.name)