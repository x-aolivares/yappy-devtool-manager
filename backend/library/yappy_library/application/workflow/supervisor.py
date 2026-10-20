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
import time
from pathlib import Path
from typing import Callable

from yappy_library.adapters import process_identity, process_tracker
from yappy_library.adapters.kafka.diagnose import (
    FIX_CLEAN_STORAGE,
    failure_excerpt,
    fix_for,
    log_dirs,
)
from yappy_library.adapters.logging import error as log_error
from yappy_library.adapters.logging import info as log_info
from yappy_library.application.workflow.readiness import DEFAULT_TIMEOUT, probe_for
from yappy_library.config import Config


def _stamp() -> str:
    """La hora, en la misma línea que el paso. El supervisor corre en un hilo
    aparte del request: sin esto no se puede ordenar contra el access log."""
    return time.strftime("%H:%M:%S")

#: How much of a failed process's own output to quote in the verdict. Three lines
#: is what a JVM stack trace, an `aws:` error and a bind failure each need to say
#: something, and a supervisor that pastes a whole log into the server console
#: has stopped being a summary.
LOG_TAIL_LINES = 3


class Supervisor:
    """Polls one readiness probe and records the verdict in the tracker."""

    def __init__(self, pid: int, port: int, probe: str, label: str = "", log_file: str = "",
                 allow_recovery: bool = True):
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
        #: Whether this supervisor may rescue a broken storage by itself.
        #:
        #: Only the first attempt gets to. The one it starts is supervised with
        #: this off, so a storage that does not get fixed stays visible with the
        #: button that cleans it instead of turning into a loop that wipes and
        #: starts every few seconds.
        self.allow_recovery = allow_recovery
        self.done = threading.Event()

    def where_to_look(self) -> str:
        return self.log_file or "el log del proceso"

    def _entry(self) -> dict | None:
        """The tracker's entry for this pid, if it is still there."""
        return next(
            (e for e in process_tracker.get_tracked_processes() if e.get("pid") == self.pid),
            None,
        )

    def still_running(self) -> bool:
        """Whether this pid is still the process that was spawned.

        Asked of the OS, not of the tracker, and that distinction is the whole
        point. A tracker entry outlives its process — it sits there until
        something prunes it, which is the ghost that ``prune_dead_on_port``
        exists to clean up. Answering "is it alive?" from the registry would
        therefore say yes to every process that ever died, and the wait would
        run the full timeout on a process that was never going to answer. This
        is the check that has to fail fast.

        The entry is still consulted for *which* process this is meant to be: a
        pid alone cannot tell a live process from a recycled one, and Windows
        recycles aggressively. Same pair ``_kill_pid`` gates on, for the same
        reason — this module never signals a pid it cannot identify.
        """
        entry = self._entry()
        if entry is None:
            # Nothing left to resolve: the row this supervisor was going to
            # write a verdict into is gone.
            return False
        live = process_identity.identity(self.pid)
        if live is None:
            return False
        expected_exe = entry.get("exe") or None
        if expected_exe and live.exe and live.exe != expected_exe:
            return False  # a different process wearing this pid
        expected_time = entry.get("create_time") or None
        if (
            expected_time
            and live.create_time
            and abs(live.create_time - expected_time)
            > process_identity.CREATE_TIME_TOLERANCE
        ):
            return False  # this pid, but not that process
        return True

    def log_tail(self, lines: int = LOG_TAIL_LINES) -> list[str]:
        """The last few lines the process itself wrote.

        Read because the reason a start failed is almost never in anything this
        module knows. A YAML file with a byte the JVM cannot decode, an expired
        SSO token, a port already taken — each is one line in the process's own
        output, and without this the verdict can only say "it did not answer",
        which sends the reader to the network when the network was never the
        problem.

        Only the tail: these logs are append-mode and a long-lived tunnel's file
        is not small. Best effort in every direction — a missing, unreadable or
        binary-ish log must never turn a failure into a crash of the reporter,
        since the reporter is a thread nobody is watching.
        """
        if not self.log_file:
            return []
        try:
            raw = Path(self.log_file).read_bytes()
        except OSError:
            return []
        # Replacement rather than strict utf-8: a process log can hold anything,
        # and a decode failure here would cost us the one line that explains
        # everything. errors="replace" keeps the line count either way.
        text = raw.decode("utf-8", errors="replace")
        kept = [line for line in text.splitlines() if line.strip()]
        return kept[-lines:]

    def verdict_log(self) -> str:
        """The failure suffix, with the process's own last words when it has any.

        Empty rather than a placeholder when there is no log to quote: a verdict
        that says "Log: " and nothing else is a worse thing to read than one
        that just does not mention it.
        """
        tail = self.log_tail()
        quoted = " | ".join(line.strip() for line in tail)
        if not quoted:
            return ""
        # ASCII on purpose, like the rest of this module: the Windows console is
        # cp1252 and a multibyte char cuts the line in half with a 'charmap'
        # error. A log that gets cut off is no use for debugging.
        quoted = quoted.encode("ascii", "replace").decode("ascii")
        # And the brackets escaped, because this project's logger prints through
        # rich, which reads `[anything]` as markup: without this a process log
        # line containing brackets does not merely lose its styling — it loses
        # text. `[ERROR]` survives by luck; `[inventado]esto` stops saying
        # "esto", and the whole point of quoting is that the text survives.
        quoted = quoted.replace("[", "\\[")
        return f" Ultimas lineas de {self.where_to_look()}: {quoted}"

    def failure_reason(self) -> str:
        """What the process's own log says went wrong, without the stack frames.

        Different from :meth:`verdict_log` on purpose. The tail is the last few
        lines whatever they are, and for a process that died on an exception the
        last lines are three frames of a Java stack trace — which say where some
        file was deleted and not one thing about why. The exception line is what
        says why, and it is not always near the end.

        Empty when the log names no failure, so the caller falls back to the tail
        instead of inventing a reason.
        """
        raw = self._log_bytes()
        if not raw:
            return ""
        excerpt = failure_excerpt(raw.decode("utf-8", errors="replace"))
        if not excerpt:
            return ""
        quoted = " | ".join(line.strip() for line in excerpt)
        quoted = quoted.encode("ascii", "replace").decode("ascii").replace("[", "\\[")
        return f" {quoted}"

    def suggested_fix(self) -> str:
        """What would fix this failure, or ``""`` if we know of nothing that would.

        Written into the tracker next to the verdict so the row can offer it as a
        button. It is a *name* and not a sentence: the wording belongs where the
        button is drawn, and the decision to offer it belongs to the module that
        knows the failure — not to the browser, which would otherwise have to
        hardcode the strings Kafka writes.
        """
        if self.probe != "kafka" or not self.log_file:
            return ""
        try:
            return fix_for(self.log_file, log_dirs(Path(Config().kafka_core_path)))
        except Exception:  # noqa: BLE001 - a diagnosis must not kill the supervisor
            return ""

    def _log_bytes(self) -> bytes:
        if not self.log_file:
            return b""
        try:
            return Path(self.log_file).read_bytes()
        except OSError:
            return b""

    def verdict(self, timeout: float) -> tuple[bool, bool]:
        """Poll the probe until it answers, the process dies, or ``timeout`` runs out.

        Returns ``(ok, died)``. The second value is the one that used to be
        missing: "nothing answered the probe" and "the process is gone" are
        different failures with different fixes, and collapsing them into one
        False is what made this module report that a dead process had opened a
        port it never reached.

        **A probe that answers is not proof that *this* process answered.** The
        port is the port: two brokers on the same one —which is exactly what
        corrupts the storage— and the probe talks to whichever of the two is
        listening. The row then says "up" for a process that died two minutes
        ago, and the developer looks at a green row while the broker he started
        is not running. So a successful probe is confirmed against the process
        before it is believed.
        """
        from yappy_library.application.workflow.readiness import wait_for

        ok = wait_for(
            lambda: probe_for(self.probe)(self.port, 2.0),
            timeout=timeout,
            abort=lambda: not self.still_running(),
        )
        if not ok:
            return False, not self.still_running()
        if self.still_running():
            return True, False
        return False, True

    def run(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        started = time.monotonic()
        who = self.label or f":{self.port}"
        # The "arranca" line that used to sit here was scaffolding for a start that was
        # failing: pid, probe, port, timeout — restating what the request being
        # answered right now already said, six lines per start. The verdicts
        # below — responds, died, opened-but-silent, gone, failed — are the ones
        # that tell you something you cannot read off the row.
        try:
            if not self.still_running():
                # It died before the probe could say anything. "Dead" beats
                # "error": the process is gone, and that is the more useful
                # thing to draw.
                fix = self.suggested_fix()
                process_tracker.update_process_state(
                    self.pid,
                    process_tracker.STATE_ERROR,
                    fix=fix,
                )
                log_info(
                    f"  [wf] {_stamp()} {who}: el proceso ya no esta, sin probe. "
                    f"Queda 'error'.{self.failure_reason() or self.verdict_log()}"
                )
                if fix == FIX_CLEAN_STORAGE:
                    # El storage también se rompe sin que el proceso llegue a
                    # escuchar: es lo mismo y se rescata igual.
                    self.recover_broken_storage()
                return

            ok, died = self.verdict(timeout)
            took = time.monotonic() - started
            fix = "" if ok else self.suggested_fix()
            process_tracker.update_process_state(
                self.pid,
                process_tracker.STATE_UP if ok else process_tracker.STATE_ERROR,
                fix=fix,
            )
            if ok:
                # ASCII en estos logs a propósito: la consola de Windows es
                # cp1252 y un "→" corta la línea a la mitad con un error de
                # 'charmap'. Un log que se corta no sirve para debuggear.
                log_info(
                    f"  [wf] {_stamp()} {who} responde en localhost:{self.port} "
                    f"en {took:.1f}s, queda 'up'"
                )
            elif died:
                # The case this module was getting wrong. The process is gone, so
                # nothing was ever listening on that port: the port is not the
                # thing to go look at, and the process's own output is.
                log_error(
                    f"  [wf] {_stamp()} {who} murio en {took:.1f}s sin llegar a "
                    f"responder; queda 'error'.{self.failure_reason() or self.verdict_log()}"
                )
            else:
                log_error(
                    f"  [wf] {_stamp()} {who} abrio el puerto {self.port} pero no respondio "
                    f"en {timeout:g}s, queda 'error'.{self.failure_reason() or self.verdict_log()}"
                )

            if not ok and fix == FIX_CLEAN_STORAGE:
                # La falla que el arreglo conocido arregla: el storage. El
                # supervisor la resuelve solo, para que el developer no tenga
                # que adivinar que el storage era el problema.
                self.recover_broken_storage()
        except Exception as exc:  # noqa: BLE001 - a supervisor must not kill the server
            log_error(f"  [wf] {_stamp()} {who}: el supervisor fallo -- {exc}")
        finally:
            self.done.set()

    def recover_broken_storage(self) -> bool:
        """Wipe the storage that broke the start and start it again.

        Only Kafka has a storage with that shape, and only Kafka has a fix we
        know how to apply, so this is the only thing that gets rescued here.

        The supervisor that watches the new broker is **not** allowed to rescue
        it again: a storage that does not get fixed has to stay on the screen
        with its button, not turn into a loop of wipes and restarts.
        """
        if self.probe != "kafka" or not self.allow_recovery:
            return False

        from yappy_library.application.workflow.recovery import recover_broken_kafka_storage

        entry = self._entry() or {}
        owner = entry.get("owner") or process_tracker.OWNER_CLI
        try:
            return recover_broken_kafka_storage(owner)
        except Exception as exc:  # noqa: BLE001 - a rescue must not kill the server
            log_error(f"  [wf] {_stamp()} no se pudo recuperar el storage de Kafka -- {exc}")
            return False


def supervise(pid: int, port: int, probe: str, label: str = "",
              timeout: float = DEFAULT_TIMEOUT, log_file: str = "",
              allow_recovery: bool = True) -> Supervisor:
    """Start a supervisor thread and return it immediately.

    Daemon, so it cannot keep the interpreter alive: a server shutting down with
    a probe in flight is fine, and the tracker entry stays ``starting``, which
    reads as "never finished" rather than as a false success.

    ``allow_recovery`` is the switch for the rescue of a broken storage: the
    first supervisor may do it, and the one it starts may not.
    """
    supervisor = Supervisor(pid, port, probe, label, log_file, allow_recovery=allow_recovery)
    thread = threading.Thread(
        target=supervisor.run,
        kwargs={"timeout": timeout},
        name=f"yappy-readiness-{port}",
        daemon=True,
    )
    thread.start()
    return supervisor


def stop_pid(pid: int) -> bool:
    """Kill a tracked process and forget it. True if something was killed.

    ``False`` covers two different situations and the screen has to be able to
    tell them apart: nothing is tracked under that pid, or a guard refused the
    kill because the pid is not provably the process we started. Only the caller
    can decide which message to show, so this stays a boolean and the reason
    stays in the log where :meth:`BaseCommand._kill_pid` wrote it.
    """
    from yappy_library.adapters.processes import BaseCommand

    if not pid:
        return False
    entries = [e for e in process_tracker.get_tracked_processes() if e.get("pid") == pid]
    if not entries:
        return False
    return BaseCommand().kill_ssm(pid)