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

from . import process_tracker
from .logger import error, success, info, warn, command as log_command, die

_DETACH_DELAY = 2


def _detach_log_path(name: str) -> Path:
    """Designated log file for a detached service: ~/.yappy/logs/<slug>.log."""
    log_dir = Path.home() / ".yappy" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return log_dir / f"{slug}.log"


@functools.lru_cache(maxsize=None)
def _aws_cmd() -> list[str]:
    """Return a working aws invocation: binary or python -m awscli."""
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
    die("AWS CLI not found. Install with: pip install awscli")


class BaseCommand:
    def serve(
        self,
        proc: subprocess.Popen,
        detach: bool = False,
        *,
        name: str = "Service",
        local_port: int | None = None,
        browser_url: str | None = None,
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
                        pid=proc.pid, resource="service", target=name,
                        log_file=str(log_path),
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
        log_command(" ".join(cmd))
        try:
            return subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
        except FileNotFoundError:
            die(f"Command not found: {cmd[0]}. Is it installed?")

    def ssm_tunnel(
        self,
        instance: str,
        port: int,
        local_port: int,
        region: str,
        profile: str,
        remote_host: str | None = None,
        quiet: bool = False,
    ) -> subprocess.Popen:
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
        devnull = subprocess.DEVNULL if quiet else None
        proc = self.popen([
            *_aws_cmd(), "ssm", "start-session",
            "--target", instance,
            "--document-name", doc,
            "--parameters", json.dumps(params),
            "--profile", profile,
            "--region", region,
        ], stdout=devnull, stderr=devnull)
        process_tracker.track_process(pid=proc.pid, resource="tunnel", target=profile)
        return proc

    @staticmethod
    def untrack_process(proc) -> None:
        try:
            process_tracker.untrack_process(proc.pid)
        except (AttributeError, OSError):
            pass

    def kill_ssm(self, pid: int | None = None):
        """Kill SSM tunnel processes.

        With `pid`: kill only that process tree. Without `pid`: kill only the
        pids tracked by the process tracker (never a global wildcard kill).
        """
        if pid is not None:
            self._kill_pid(pid)
            process_tracker.untrack_process(pid)
            return

        tracked = [
            p["pid"]
            for p in process_tracker.get_tracked_processes(resource="tunnel")
            if p.get("pid")
        ]
        if not tracked:
            info("No tracked tunnels found — use `yappy ssm kill --all`")
            return
        for p in tracked:
            self._kill_pid(p)
            process_tracker.untrack_process(p)

    def _kill_pid(self, pid: int):
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
        if sys.platform == "win32":
            os.system(f'start "" "{url}"')
        else:
            import webbrowser
            webbrowser.open(url)

    def check_requirements(self, *cmds: str):
        missing = []
        for c in cmds:
            if c == "aws":
                try:
                    _aws_cmd()
                except SystemExit:
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
        from .config import Config
        known = Config.known_environments()
        if not known:
            return
        if env not in known:
            die(f"Unknown environment '{env}'. Available: {', '.join(known)}")
