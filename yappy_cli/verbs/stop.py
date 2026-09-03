from __future__ import annotations

import typer

stop_app = typer.Typer(help="Stop a resource")


@stop_app.command(name="kafka")
def stop_kafka(
    target: str = typer.Argument("server", help="server or ui"),
):
    """Stop local Kafka server or UI."""
    from ..kafka.manager import down as _old_down
    _old_down(target, quiet_deprecation=True)


@stop_app.command(name="tunnel")
def stop_tunnel():
    """Kill all active SSM sessions."""
    from ..ssm.tunnel import kill as _old_kill
    _old_kill(quiet_deprecation=True)
