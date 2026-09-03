from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .logger import info

_TRACKER_DIR = Path.home() / ".yappy" / "tracker"


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
            try:
                os.kill(pid, 0)
                data["alive"] = True
            except (OSError, ProcessLookupError):
                data["alive"] = False

        results.append(data)

    return results


def untrack_process(pid: int):
    f = _TRACKER_DIR / f"{pid}.json"
    if f.exists():
        f.unlink()
