from __future__ import annotations

import typer

stop_app = typer.Typer(help="Stop a resource")


@stop_app.command(name="kafka")
def stop_kafka(
    target: str = typer.Argument("server", help="server or ui"),
):
    """Stop local Kafka server or UI."""
    from library.kafka.manager import down as _old_down
    _old_down(target, quiet_deprecation=True)


@stop_app.command(name="tunnel")
def stop_tunnel():
    """Kill all active SSM sessions."""
    from library.ssm.tunnel import kill as _old_kill
    _old_kill(quiet_deprecation=True)


@stop_app.command(name="web")
def stop_web():
    """Stop a web devtool left running (API / UI) after its terminal died."""
    from library import process_tracker
    from library.logger import die, info, success

    tracked = process_tracker.get_tracked_processes(resource="web")
    if not tracked:
        info("No web processes found — `yappy web` already cleaned up on Ctrl+C.")
        return

    for proc in tracked:
        pid = proc.get("pid")
        if not pid:
            continue
        name = proc.get("target", "web")
        if not proc.get("alive"):
            process_tracker.untrack_process(pid)
            info(f"  {name} (PID {pid}) was already dead — cleaned up stale entry")
            continue

        from .web import _kill_tree

        _kill_tree(pid)
        process_tracker.untrack_process(pid)
        success(f"  {name} (PID {pid}) stopped")
