"""Turning "the process exists" into "the tunnel is up".

``ssm start-session`` returns as soon as the plugin is running, which is before
the data channel exists. The port binds either way. So a start that returns
after the spawn has told the caller nothing useful, and a screen that trusted
that would paint a dead tunnel green.

So a start is three steps, and only the first one is synchronous:

1. spawn the process and write a ``starting`` entry to the tracker,
2. hand the process to a supervisor thread and return,
3. the supervisor polls the readiness probe and writes ``up`` or ``error``.

The split is deliberate: the probe can take tens of seconds and the caller — an
HTTP request, or a button in a browser — should not be holding a connection open
for all of it. The screen polls the tracker and watches the row move on its own,
which is also what makes the state survive the browser tab closing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from yappy_library.adapters import process_tracker
from yappy_library.adapters.logging import error as log_error
from yappy_library.adapters.logging import info as log_info
from yappy_library.application.workflow.readiness import DEFAULT_TIMEOUT, probe_for

#: Never let a supervisor outlive the process it watches by much. The plugin is
#: killed by `stop`; this is only the backstop for a start that failed in a way
#: that left no process to check against.
_MAX_SUPERVISED_SECONDS = DEFAULT_TIMEOUT * 2


class Supervisor:
    """Polls one readiness probe and records the verdict in the tracker."""

    def __init__(self, pid: int, port: int, probe: str, label: str = "", log_file: str = ""):
        self.pid = pid
        self.port = port
        self.probe = probe
        self.label = label
        #: Where this process's output goes. Recorded, not assumed: a tunnel logs
        #: to `~/.yappy/logs/tunnel-<port>.log` but Kafka logs to its own
        #: `temp-logs/kafka-<target>-<pid>.log` under the install, and pointing at
        #: the tunnel path for a Kafka failure sends the reader to a file that
        #: does not exist.
        self.log_file = log_file
        self.done = threading.Event()

    def where_to_look(self) -> str:
        return self.log_file or "el log del proceso"

    def verdict(self, timeout: float) -> bool:
        """Poll the probe until it answers or ``timeout`` runs out."""
        from yappy_library.application.workflow.readiness import wait_for

        return wait_for(lambda: probe_for(self.probe)(self.port, 2.0), timeout=timeout)

    def run(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        try:
            alive = process_tracker.get_tracked_processes()
            still_there = any(e.get("pid") == self.pid for e in alive)

            if not still_there:
                # It died before the probe could say anything. "Dead" beats
                # "error": the process is gone, and that is the more useful
                # thing to draw.
                process_tracker.update_process_state(self.pid, process_tracker.STATE_ERROR)
                log_info(f"El proceso de {self.label or self.port} terminó antes de levantar")
                return

            ok = self.verdict(timeout)
            process_tracker.update_process_state(
                self.pid, process_tracker.STATE_UP if ok else process_tracker.STATE_ERROR
            )
            if ok:
                log_info(f"{self.label or self.port} responds on localhost:{self.port}")
            else:
                log_error(
                    f"{self.label or self.port} opened port {self.port} but did not respond "
                    f"in {timeout:g}s. The log is in {self.where_to_look()}"
                )
        except Exception as exc:  # noqa: BLE001 - a supervisor must not kill the server
            log_error(f"No se pudo verificar {self.label or self.port}: {exc}")
        finally:
            self.done.set()


def supervise(pid: int, port: int, probe: str, label: str = "",
              timeout: float = DEFAULT_TIMEOUT, log_file: str = "") -> Supervisor:
    """Start a supervisor thread and return it immediately.

    Daemon, so it cannot keep the interpreter alive: a server shutting down with
    a probe in flight is fine, and the tracker entry stays ``starting``, which
    reads as "never finished" rather than as a false success.
    """
    supervisor = Supervisor(pid, port, probe, label, log_file)
    thread = threading.Thread(
        target=supervisor.run,
        kwargs={"timeout": timeout},
        name=f"yappy-readiness-{port}",
        daemon=True,
    )
    thread.start()
    return supervisor


def stop_pid(pid: int) -> bool:
    """Kill a tracked process and forget it. True if something was killed."""
    from yappy_library.adapters.processes import BaseCommand

    if not pid:
        return False
    entries = [e for e in process_tracker.get_tracked_processes() if e.get("pid") == pid]
    if not entries:
        return False
    BaseCommand().kill_ssm(pid)
    return True