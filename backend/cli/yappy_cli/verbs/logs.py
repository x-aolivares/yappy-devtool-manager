from __future__ import annotations

import time
from pathlib import Path

import typer

from yappy_library.adapters.log_tail import read_from, tail_start_offset
from yappy_library.adapters.logging import console, info

logs_app = typer.Typer(help="Show logs of managed processes")


def tail_log(log_path: Path, follow: bool, lines: int):
    """Print the last `lines` lines of a log file.

    With follow=True, keep polling the file for appended lines every second
    until KeyboardInterrupt. The offset of the already-printed bytes is
    recorded so only new content is emitted on each poll.

    The offset arithmetic lives in `adapters/log_tail` so the Workflow screen can
    do the same thing against a browser instead of a terminal.
    """
    data = log_path.read_bytes()
    offset = tail_start_offset(data, lines)
    tail = data[offset:].decode("utf-8", errors="replace")
    for line in tail.splitlines():
        console.print(line)

    if not follow:
        return

    try:
        while True:
            time.sleep(1)
            chunk, offset = read_from(log_path, offset)
            for line in chunk.splitlines():
                if line:
                    console.print(line)
    except KeyboardInterrupt:
        return


@logs_app.command(name="db")
def logs_db(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Show logs for DB tunnel."""
    _show_logs("db", env, follow, lines)


@logs_app.command(name="kafka")
def logs_kafka(
    target: str = typer.Argument(..., help="server or ui"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Show logs for Kafka server or UI."""
    _show_logs("kafka", target, follow, lines)


@logs_app.command(name="tunnel")
def logs_tunnel(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Show logs for SSM tunnels."""
    _show_logs("tunnel", "", follow, lines)


def _show_logs(resource: str, target: str, follow: bool, lines: int):
    from yappy_library.adapters.process_tracker import get_tracked_processes
    processes = get_tracked_processes(resource=resource, target=target)

    if not processes:
        info(f"No tracked processes found for '{resource}'.")
        info("Processes are tracked automatically when started with yappy run/stop.")
        return

    if follow:
        info("Press Ctrl+C to stop following")

    for proc in processes:
        log_file = proc.get("log_file", "")
        pid = proc.get("pid", "?")
        alive = proc.get("alive", False)
        status = "alive" if alive else "dead"
        info(f"Process {pid} ({resource}/{target}) — {status}")

        if log_file and Path(log_file).exists():
            tail_log(Path(log_file), follow=follow, lines=lines)
        else:
            info(f"  No log file available for PID {pid}.")
