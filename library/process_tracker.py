from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .logger import info

_TRACKER_DIR = Path.home() / ".yappy" / "tracker"

#: Enough to open a handle for querying, without asking for full control.
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: ERROR_ACCESS_DENIED: the handle was refused, but the process does exist.
_WIN_ERROR_ACCESS_DENIED = 5


def _pid_alive(pid: int) -> bool:
    """Is this PID still running?

    NOT `os.kill(pid, 0)`. Per the Python docs, on Windows "any other value for
    sig will cause the process to be unconditionally killed by the
    TerminateProcess API" — 0 is not a special case there, so the idiomatic
    POSIX liveness probe silently kills the very process we were inspecting.
    Every `yappy status` / `yappy stop` would take its targets down just by
    looking at them.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                _WIN_PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # Exists but is not ours to open — still alive.
            return ctypes.get_last_error() == _WIN_ERROR_ACCESS_DENIED
        except Exception:  # noqa: BLE001 - a wrong answer here is not fatal
            return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # ESRCH: no such process
    except PermissionError:
        return True  # EPERM: it exists, it is just not ours to signal
    except OSError:
        return False
    return True


def track_process(
    pid: int,
    resource: str,
    target: str,
    env: str = "",
    log_file: str | None = None,
):
    _TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "pid": pid,
        "resource": resource,
        "target": target,
        "env": env,
        "started_at": time.time(),
        "log_file": log_file or "",
    }
    (_TRACKER_DIR / f"{pid}.json").write_text(json.dumps(entry, indent=2))


def get_tracked_processes(
    resource: str | None = None,
    target: str | None = None,
) -> list[dict]:
    results = []
    if not _TRACKER_DIR.exists():
        return results

    for f in sorted(_TRACKER_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if resource and data.get("resource") != resource:
            continue
        if target and data.get("target") != target:
            continue

        pid = data.get("pid")
        if pid:
            data["alive"] = _pid_alive(pid)

        results.append(data)

    return results


def untrack_process(pid: int):
    f = _TRACKER_DIR / f"{pid}.json"
    if f.exists():
        f.unlink()
