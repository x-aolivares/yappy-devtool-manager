from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import typer

from yappy_library.adapters import process_tracker
from yappy_library.adapters.database.credentials import (
    generate_token as _generate_token,
    write_local_env as _write_local_env,
)
from yappy_library.adapters.processes import BaseCommand
from yappy_library.config import Config
from ..deprecation import warn_deprecated
from yappy_library.adapters.logging import die, info, success, warn

app = typer.Typer(help="Database tunnel management")
REFRESH_INTERVAL = 12 * 60  # 12 minutes (token expires in 15)


class DbCommand(BaseCommand):
    pass


db_cmd = DbCommand()


def _start_refresher(cfg: Config, stop_event: threading.Event):
    def refresher():
        while not stop_event.wait(REFRESH_INTERVAL):
            info("Refreshing DB token...")
            try:
                new_token = _generate_token(cfg)
                _write_local_env(new_token)
                success("Token refreshed (valid for ~15 more min)")
            except Exception as e:
                warn(f"Token refresh failed: {e}")
    t = threading.Thread(target=refresher, daemon=True)
    t.start()


def _start_detached_refresher(env: str) -> Path:
    """Spawn a detached child that refreshes the DB token after the CLI exits."""
    log_dir = Path.home() / ".yappy" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"db-refresher-{env}.log"
    cmd = [sys.executable, "-m", "yappy_cli.db.refresher", env]
    kwargs = {
        "stdout": log_path.open("ab"),
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    child = subprocess.Popen(cmd, **kwargs)
    process_tracker.track_process(
        pid=child.pid,
        resource="tunnel",
        target=env,
        log_file=str(log_path),
    )
    return log_path


@app.command()
def refresh(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    quiet_deprecation: bool = False,
):
    """Regenerate DB auth token and save to .env.local."""
    if not quiet_deprecation:
        warn_deprecated("db refresh", "run db --refresh")
    DbCommand.validate_env(env)
    cfg = Config.with_env(env)

    info(f"Generating new DB token for {env}...")
    token = _generate_token(cfg)
    _write_local_env(token)
    info(f"Token expires in ~15 min. Run 'yappy db refresh {env}' to renew.")


@app.command()
def up(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    auto_refresh: bool = typer.Option(
        False, "--auto-refresh", "-r",
        help="Auto-refresh token every 12 minutes",
    ),
    detach: bool = typer.Option(
        False, "--detach", "-d",
        help="Run tunnel in background",
    ),
    keep_alive: bool = typer.Option(
        False, "--keep-alive", "-k",
        help="Auto-reconnect tunnel if it drops",
    ),
    quiet_deprecation: bool = False,
):
    """Start SSM tunnel to Aurora database."""
    if not quiet_deprecation:
        warn_deprecated("db up", "run db")
    if keep_alive and detach:
        die("--keep-alive and --detach are mutually exclusive")
    DbCommand.validate_env(env)
    db_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    token = _generate_token(cfg)
    _write_local_env(token)

    if auto_refresh:
        info(f"Starting auto-refresh tunnel to {env} (localhost:{cfg.db_port})...")
    else:
        info(f"Starting database tunnel to {env} (localhost:{cfg.db_port})...")

    try:
        proc = db_cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=int(cfg.get("AWS_PORT", "53360")),
            local_port=cfg.db_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=cfg.require("AWS_HOST"),
            quiet=detach,
        )
    except Exception as e:
        die(f"Failed to start tunnel: {e}")

    def _restart():
        info("Regenerating token and restarting tunnel...")
        new_token = _generate_token(cfg)
        _write_local_env(new_token)
        return db_cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=int(cfg.get("AWS_PORT", "53360")),
            local_port=cfg.db_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=cfg.require("AWS_HOST"),
        )

    if keep_alive:
        stop_event: threading.Event | None = None
        if auto_refresh:
            stop_event = threading.Event()
            _start_refresher(cfg, stop_event)
        try:
            db_cmd.serve_forever(proc, name=f"DB tunnel to {env}", local_port=cfg.db_port, on_restart=_restart)
        finally:
            if stop_event:
                stop_event.set()
        return

    if auto_refresh:
        success(f"Tunnel started (PID {proc.pid}) on localhost:{cfg.db_port}")

        if detach:
            _start_detached_refresher(env)
            success("Auto-refresh tunnel running in background (use 'yappy ssm kill' to stop)")
            time.sleep(2)
            return

        stop_event = threading.Event()
        _start_refresher(cfg, stop_event)
        info("Press Ctrl+C to stop tunnel and refresher")
        try:
            while True:
                time.sleep(2)
                if proc.poll() is not None:
                    die(f"SSM tunnel exited unexpectedly (code {proc.returncode})")
        except KeyboardInterrupt:
            info("Stopping tunnel...")
            stop_event.set()
            proc.terminate()
            proc.wait(timeout=5)
            db_cmd.kill_ssm()
            process_tracker.untrack_process(proc.pid)
            success("Tunnel stopped")
        return

    db_cmd.serve(proc, detach, name="DB tunnel", local_port=cfg.db_port)
