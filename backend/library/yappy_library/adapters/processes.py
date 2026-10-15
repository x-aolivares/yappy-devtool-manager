from __future__ import annotations

import functools
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from yappy_library.adapters import process_identity, process_tracker
from yappy_library.adapters.logging import (
    command as log_command,
    die,
    error,
    info,
    success,
    warn,
)

_DETACH_DELAY = 2


class ToolUnavailable(Exception):
    """An external tool this module shells out to is missing or unusable.

    **Why this exists, and why it is not ``die()``.** ``die()`` ends the
    *process*, which is the right answer for a command and a fatal one for
    anything the web server drives. ``POST /api/workflow/ports/start`` is a sync
    ``def``, so Starlette runs it in an anyio worker thread; anyio re-raises
    whatever came out into the event-loop task, ``asyncio.Task.__step``
    re-raises ``SystemExit`` explicitly (it is a ``BaseException``, so no
    ``except Exception`` in the stack can catch it), and it escapes
    ``run_until_complete``. uvicorn is gone — no traceback, nothing in the log,
    the UI simply stops existing. The same shape as the ``taskkill /T`` cascade.

    This module is the most exposed version of that: :func:`_aws_cmd` runs on
    *every* tunnel the web opens (``actions._spawn_tunnel`` →
    :meth:`BaseCommand.ssm_tunnel`) and on every DB request
    (``connection.connect``), so the trigger is not a broken Kafka install but a
    missing or misconfigured AWS CLI — an ordinary machine state.

    **What a caller does with it is its own decision, and each one is right for a
    different reason:**

    - the Typer app turns it back into the exit it always was, with the same
      message and the same code (``cli/yappy_cli/cli.py``) — a command has many
      entry points and one place that knows how to exit;
    - ``actions._spawn_tunnel`` turns it into a ``StartRefused``, which the route
      already answers with a 409 carrying the reason;
    - ``connection.connect`` turns it into a ``SyncError``, which every DB route
      already knows how to render as an error row.

    The reason travels in English inside the library so the terminal keeps
    printing exactly what it printed before; only the wrapper the screen reads is
    Spanish. The invariant that no ``die()`` comes back on a web-reachable path
    is pinned by ``tests/test_tool_unavailable.py``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _detach_log_path(name: str) -> Path:
    """Designated log file for a detached service: ~/.yappy/logs/<slug>.log."""
    log_dir = Path.home() / ".yappy" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return log_dir / f"{slug}.log"


def tunnel_log_path(local_port: int) -> Path:
    """Where the AWS CLI output of the tunnel on ``local_port`` is kept.

    ``~/.yappy/logs/tunnel-<port>.log``, truncated per attempt: the log you want
    when a tunnel misbehaves is the one belonging to the tunnel that just
    started, not an append-only history of every attempt.
    """
    return _detach_log_path(f"tunnel-{local_port}")


def detached_popen_kwargs() -> dict:
    """The ``Popen`` keywords that put a child outside our console and its group.

    **Why this is a helper and not three copies of the same four lines.** Windows
    gives every process a console group, and a child inherits the one of whoever
    launched it unless it is told otherwise. That is why a broker started from the
    web used to go down with the web: ``Ctrl+C`` in the group reaches the whole
    group, and the broker was in it. The flags below are the way out, and the cost
    of forgetting them once per call site is a process that shares our fate.

    ``DETACHED_PROCESS`` gives the child no console at all — no window flashes on
    the developer's desktop, and nothing it prints lands in our terminal.
    ``CREATE_NEW_PROCESS_GROUP`` makes it the root of a *new* group, so the group
    events we receive are not delivered to it. The two are not interchangeable: the
    first alone leaves the child in our group.

    On POSIX the equivalent is ``start_new_session``, which calls ``setsid``. The
    caller always passes a list and never a shell string, so nothing here changes
    how the arguments are quoted.
    """
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        }
    return {"start_new_session": True}


def open_url(url: str) -> None:
    """Open ``url`` in the default browser, from wherever we happen to be running.

    This used to be ``os.system(f'start "" "{url}"')``, and ``os.system`` is the
    one call in this module that cannot be made safe: it hands the string to
    ``system()``, which on Windows spawns ``cmd.exe`` **inheriting our console and
    our process group**. So the browser landed in the same group as uvicorn — the
    exact group ``detached_popen_kwargs`` exists to escape — and the window that
    popped up belonged to the server's console rather than to the developer's
    session. It also meant the caller had no way to opt out: unlike the Kafdrop
    opener, this one never looked at who was asking.

    The visible behaviour is unchanged: the browser opens, and a URL with spaces
    still opens correctly.

    **Why the Windows branch is ``["cmd", "/c", "start", "", url]`` and not
    ``["start", url]``:**

    - ``start`` is a ``cmd.exe`` *builtin*. There is no ``start.exe`` on disk, so
      ``Popen`` would raise ``FileNotFoundError``. ``/c`` is how you run a builtin
      from an external command.
    - The empty string is ``start``'s mandatory *title* argument, and it is the
      whole trick. ``start`` treats its first quoted token as the window title. The
      old string was ``start "" "http://..."`` for that reason: without the empty
      title, the quoted URL is swallowed as the title and ``start`` opens nothing,
      exiting happily. ``Popen`` quotes the URL itself when it contains a space,
      so the argument list reproduces the old string's semantics exactly.
    - The child is never waited on. ``start`` returns as soon as it has handed the
      URL to the shell, and blocking a request thread on a browser launch is the
      thing this replaces.

    On POSIX ``webbrowser.open`` is what both callers used before and is kept.
    """
    if sys.platform == "win32":
        subprocess.Popen(
            ["cmd", "/c", "start", "", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached_popen_kwargs(),
        )
    else:
        import webbrowser

        webbrowser.open(url)


@functools.lru_cache(maxsize=None)
def _aws_cmd() -> list[str]:
    """Return a working aws invocation: binary or python -m awscli.

    Raises :class:`ToolUnavailable` when neither works. It used to ``die()``
    here, and ``die()`` from this function was the single most reachable silent
    uvicorn kill in the codebase: every tunnel and every DB query goes through
    it. The terminal still ends up in exactly the same place — see the
    ``_YappyTyper`` handler in ``cli/yappy_cli/cli.py``.
    """
    try:
        r = subprocess.run(["aws", "--version"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return ["aws"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(
            [sys.executable, "-m", "awscli", "--version"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            return [sys.executable, "-m", "awscli"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise ToolUnavailable("AWS CLI not found. Install with: pip install awscli")


def _expected_identity(entry: dict | None) -> dict:
    """The identity a tracker entry claims, as ``_kill_pid`` keyword arguments.

    Missing fields come back as ``None`` and that is the whole point: a tracker
    file written before this feature existed has no opinion about what its pid
    was, and *no opinion* must not be turned into a refusal. Those entries get
    the ancestor guard alone — weaker than we want, but refusing to stop a live
    tunnel because we cannot prove it is the tunnel would be worse.
    """
    if not entry:
        return {"expected_exe": None, "expected_create_time": None}
    return {
        "expected_exe": entry.get("exe") or None,
        "expected_create_time": entry.get("create_time") or None,
    }


def _tracked_entry(pid: int) -> dict | None:
    """The tracker entry for ``pid``, if the registry still has one."""
    for entry in process_tracker.get_tracked_processes():
        if entry.get("pid") == pid:
            return entry
    return None


class BaseCommand:
    def serve(
        self,
        proc: subprocess.Popen,
        detach: bool = False,
        *,
        name: str = "Service",
        local_port: int | None = None,
        browser_url: str | None = None,
        env: str = "",
    ):
        if detach:
            log_path = _detach_log_path(name)
            log_path.touch(exist_ok=True)
            port_str = f" on localhost:{local_port}" if local_port else ""
            success(f"{name} started (PID {proc.pid}){port_str}")
            try:
                already_tracked = any(
                    p.get("pid") == proc.pid
                    for p in process_tracker.get_tracked_processes()
                )
                if not already_tracked:
                    process_tracker.track_process(
                        pid=proc.pid,
                        resource="service",
                        target=name,
                        env=env,
                        log_file=str(log_path),
                        local_port=local_port,
                    )
            except (AttributeError, OSError):
                pass
            if browser_url:
                self.open_browser(browser_url)
            time.sleep(_DETACH_DELAY)
            return

        if browser_url:
            self.open_browser(browser_url)
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            self.kill_ssm()
            success(f"{name} closed")
        finally:
            self.untrack_process(proc)

    def serve_forever(
        self,
        proc: subprocess.Popen,
        *,
        name: str = "Service",
        local_port: int | None = None,
        on_restart: Callable[[], subprocess.Popen],
    ):
        """Run a tunnel in a reconnect loop. Press Ctrl+C to stop."""
        port_str = f" on localhost:{local_port}" if local_port else ""
        info(f"{name} started{port_str} (keep-alive mode)")
        info("Press Ctrl+C to stop")
        try:
            while True:
                try:
                    exit_code = proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
                    self.kill_ssm()
                    success(f"{name} closed")
                    return
                if exit_code == 0:
                    success(f"{name} exited cleanly")
                    return
                warn(f"{name} exited (code {exit_code}), restarting in 2s...")
                time.sleep(2)
                self.untrack_process(proc)
                try:
                    proc = on_restart()
                except Exception as e:
                    die(f"Failed to restart {name}: {e}")
        finally:
            self.untrack_process(proc)

    def run(
        self,
        cmd: list[str],
        capture: bool = False,
        shell: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        log_command(" ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                shell=shell,
                check=check,
            )
            if result.returncode != 0 and check:
                error(f"Command failed (exit {result.returncode})")
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
            return result
        except FileNotFoundError:
            die(f"Command not found: {cmd[0]}. Is it installed?")
        except Exception as e:
            die(f"Error running command: {e}")

    def popen(
        self,
        cmd: list[str],
        stdout=None,
        stderr=None,
    ) -> subprocess.Popen:
        """Spawn ``cmd``, or say the binary is not there.

        The message is the one this always printed. What changed is the type:
        :class:`ToolUnavailable` instead of ``SystemExit``, because
        :meth:`ssm_tunnel` calls this and ``ssm_tunnel`` is what the web calls
        from a sync endpoint. ``FileNotFoundError`` here means the OS refused the
        exact binary that ``_aws_cmd()`` just ran successfully — a PATH race, a
        reinstall in progress, an AppLocker refusal — so it is rare, but it is
        not rarer than the thing this class was written for.
        """
        log_command(" ".join(cmd))
        try:
            return subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
        except FileNotFoundError:
            raise ToolUnavailable(f"Command not found: {cmd[0]}. Is it installed?") from None

    def ssm_tunnel(
        self,
        instance: str,
        port: int,
        local_port: int,
        region: str,
        profile: str,
        remote_host: str | None = None,
        quiet: bool = False,
        log_file: str | Path | None = None,
        env: str = "",
        cluster: str = "",
        label: str = "",
        owner: str = process_tracker.OWNER_CLI,
    ) -> subprocess.Popen:
        """Start one ``ssm start-session`` forwarding ``port`` to ``local_port``.

        ``env``/``cluster``/``label`` are not needed to start anything. They are
        recorded so a tracked tunnel can be attributed back to the row it belongs
        to — see :mod:`yappy_library.adapters.process_tracker`, which is the
        whole reason they are here.
        """
        if remote_host and sys.platform == "linux":
            # Was `sys.exit(1)`, bare, with nothing written anywhere. On Linux,
            # starting a remote-host tunnel therefore ended *uvicorn*: this
            # function is reached from `actions._spawn_tunnel`, which backs the
            # sync `POST /api/workflow/ports/start`, and `SystemExit` is a
            # `BaseException` — it does not fail the request, it escapes the
            # Starlette worker thread into the event loop and takes the server
            # down with no traceback. Raised, the route turns it into a 409 and
            # the row says why; from a terminal it still exits 1 through the
            # Typer handler.
            raise ToolUnavailable(
                "El port-forwarding a un host remoto no anda en Linux. "
                "Levantalo desde una sesión de Windows o usá un túnel sin host."
            )
        if remote_host:
            doc = "AWS-StartPortForwardingSessionToRemoteHost"
            params = {
                "portNumber": [str(port)],
                "host": [remote_host],
                "localPortNumber": [str(local_port)],
            }
        else:
            doc = "AWS-StartPortForwardingSession"
            params = {
                "portNumber": [str(port)],
                "localPortNumber": [str(local_port)],
            }

        # `quiet` used to mean DEVNULL, which also swallowed the AWS CLI's own
        # diagnostics. A tunnel that failed to bind its local port then looked
        # exactly like one that was still coming up, which is how a request ends
        # up waiting forever on a tunnel that was never going to work.
        handle = None
        if log_file:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "w", encoding="utf-8", errors="replace")
            sink = handle
        else:
            sink = subprocess.DEVNULL if quiet else None

        try:
            proc = self.popen([
                *_aws_cmd(), "ssm", "start-session",
                "--target", instance,
                "--document-name", doc,
                "--parameters", json.dumps(params),
                "--profile", profile,
                "--region", region,
            ], stdout=sink, stderr=sink)
        finally:
            if handle is not None:
                handle.close()
        process_tracker.track_process(
            pid=proc.pid,
            resource="tunnel",
            target=profile,
            env=env,
            log_file=str(log_file) if log_file else "",
            local_port=local_port,
            remote_port=port,
            remote_host=remote_host or "",
            cluster=cluster,
            label=label,
            owner=owner,
        )
        return proc

    @staticmethod
    def untrack_process(proc) -> None:
        try:
            process_tracker.untrack_process(proc.pid)
        except (AttributeError, OSError):
            pass

    def kill_ssm(self, pid: int | None = None) -> bool:
        """Kill SSM tunnel processes.

        With `pid`: kill only that process tree. Without `pid`: kill only the
        pids tracked by the process tracker (never a global wildcard kill).

        Returns whether a kill was actually attempted. A ``False`` means a guard
        in :meth:`_kill_pid` refused, which is the one answer a caller must not
        turn into "parado": the process is still running, and a screen that says
        otherwise teaches the developer to click the button again.
        """
        if pid is not None:
            killed = self._kill_pid(pid, **_expected_identity(_tracked_entry(pid)))
            if killed:
                process_tracker.untrack_process(pid)
            return killed

        tracked = [
            p
            for p in process_tracker.get_tracked_processes(resource="tunnel")
            if p.get("pid")
        ]
        if not tracked:
            info("No tracked tunnels found — use `yappy ssm kill --all`")
            return False
        killed = False
        for entry in tracked:
            if self._kill_pid(entry["pid"], **_expected_identity(entry)):
                killed = True
                process_tracker.untrack_process(entry["pid"])
        return killed

    def _kill_pid(
        self,
        pid: int,
        *,
        expected_exe: str | None = None,
        expected_create_time: float | None = None,
    ) -> bool:
        """Kill a process tree, but only after proving that pid is ours.

        ``taskkill /t`` walks the tree *upwards and downwards* from the target, so
        a pid that no longer belongs to the tunnel we tracked is enough to take
        down whatever process tree that pid happens to be in now. On Windows pid
        reuse is fast and the tracker is a file on disk that outlives the process
        it names: a stale entry from a crashed session can, hours later, name a
        pid that the shell or the web server has since been given.

        Killing an ancestor of ourselves does not raise — it ends the process,
        and the console reports it as a Ctrl+C that nobody pressed. That is the
        worst possible failure mode for a "stop this tunnel" button: it looks like
        the app crashed and there is nothing in its own log to explain it.

        Three gates, weakest reason first. The ancestor check is unconditional —
        it is the one that protects *us*. The identity checks only apply when the
        tracker recorded what the pid was; an entry written before those fields
        existed gets the ancestor check and nothing else, because refusing to stop
        a real tunnel on the grounds that we cannot identify it would trade a rare
        wrong kill for a permanent leak.

        Returns whether the kill was actually attempted. Callers ignore this
        today; it exists so a refusal is distinguishable from a silent success.
        """
        if not pid:
            return False

        parent_map = process_identity.parent_map()
        if pid in self._own_family(parent_map):
            error(
                f"El pid {pid} pertenece a esta sesión (o a un proceso que la "
                f"contiene). No se para: `taskkill /t` se llevaría también al "
                f"proceso que está tratando de cerrarlo."
            )
            return False

        live = process_identity.identity(pid)

        if expected_exe and live and live.exe and live.exe != expected_exe:
            error(
                f"El pid {pid} ahora es {live.exe}, y el tracker lo había "
                f"registrado como {expected_exe}. No se para: ese pid ya no "
                f"pertenece al proceso que se levantó."
            )
            return False

        if (
            expected_create_time
            and live
            and live.create_time
            and abs(live.create_time - expected_create_time)
            > process_identity.CREATE_TIME_TOLERANCE
        ):
            error(
                f"El pid {pid} se creó {abs(live.create_time - expected_create_time):.0f}s "
                f"después de lo que el tracker registró. No se para: es otro "
                f"proceso con el mismo pid."
            )
            return False

        if not expected_exe and not expected_create_time:
            info(
                f"El pid {pid} no tiene identidad registrada (tracker anterior a esta "
                f"versión): sólo se verificó que no sea ancestro de esta sesión."
            )

        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/pid", str(pid), "/t", "/f"],
                capture_output=True,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        return True

    @staticmethod
    def _own_family(parent_map: dict[int, int] | None = None) -> set[int]:
        """This process and its ancestors, read from the OS.

        Deliberately only *upwards*. A sibling or a child is a legitimate thing
        to stop — the refreshers and the tunnels this tool starts are both — and
        refusing them would break `stop_all`. What must never happen is stopping
        something that contains us.

        Why :mod:`yappy_library.adapters.process_identity` and not ``os.getppid()``:
        ``os.getppid()`` takes no arguments. It reports the parent of *this*
        process and nothing else, so assigning it to a loop variable and calling
        it again returns the same value every iteration. The version that did
        that returned exactly ``{os.getpid(), os.getppid()}`` no matter how deep
        the real chain went — a guard that looks like it checks a tree and in
        fact checks two pids, which is why a web server could kill itself over a
        tracker entry nobody had looked at in a week.

        Falls back to ``{os.getpid(), os.getppid()}`` when the platform will not
        give us a map. That is the *old*, broken behaviour and it is kept
        deliberately: a guard that refuses everything because it could not read
        the process table would make "stop this tunnel" never work, and two pids
        is strictly more than not trying.
        """
        if parent_map is None:
            parent_map = process_identity.parent_map()

        me = os.getpid()
        if not parent_map or me not in parent_map:
            warn(
                "No se pudo leer el mapa de procesos del sistema; la protección "
                "de ancestros queda reducida al proceso actual y a su padre "
                "directo."
            )
            family = {me}
            try:
                family.add(os.getppid())
            except (AttributeError, OSError):
                pass
            return family

        return process_identity.ancestors(me, parent_map=parent_map)

    def kill_ssm_all(self):
        """Legacy global kill of every session-manager-plugin process."""
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/f", "/im", "session-manager-plugin.exe"],
                capture_output=True,
            )
        else:
            subprocess.run(["pkill", "session-manager-plugin"], capture_output=True)

    def open_browser(self, url: str):
        """Open ``url``; see :func:`open_url` for why this is not ``os.system``."""
        open_url(url)

    def check_requirements(self, *cmds: str):
        """Fail with one message listing every missing tool, not the first one.

        The ``aws`` arm is not ``_which("aws")`` on purpose: ``_aws_cmd`` also
        accepts ``python -m awscli``, so a machine with no ``aws`` on PATH is
        perfectly usable and must not be reported as missing. It is also the one
        caller that *wants* the failure — it is a preflight check, so it turns
        the refusal into one line instead of letting the first command die later
        halfway through a workflow.
        """
        missing = []
        for c in cmds:
            if c == "aws":
                try:
                    _aws_cmd()
                except ToolUnavailable:
                    missing.append("aws")
            elif not self._which(c):
                missing.append(c)
        if missing:
            die(
                f"Missing required tools: {', '.join(missing)}. "
                f"Please install them first."
            )

    @staticmethod
    def _which(cmd: str) -> bool:
        result = subprocess.run(
            ["which", cmd] if sys.platform != "win32" else ["where", cmd],
            capture_output=True,
            shell=True,
        )
        return result.returncode == 0

    @staticmethod
    def validate_env(env: str):
        from yappy_library.config import Config
        known = Config.known_environments()
        if not known:
            return
        if env not in known:
            die(f"Unknown environment '{env}'. Available: {', '.join(known)}")
