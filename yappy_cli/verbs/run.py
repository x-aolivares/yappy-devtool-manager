from __future__ import annotations

import typer

from ..logger import die

run_app = typer.Typer(help="Start a resource")


@run_app.command(name="db")
def run_db(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    keep_alive: bool = typer.Option(False, "--keep-alive", "-k", help="Auto-reconnect tunnel if it drops"),
    auto_refresh: bool = typer.Option(False, "--auto-refresh", "-r", help="Auto-refresh token every 12 minutes"),
):
    """Start SSM tunnel to Aurora database."""
    from ..db.tunnel import up as _old_db_up
    _old_db_up(env, auto_refresh=auto_refresh, detach=detach, keep_alive=keep_alive, quiet_deprecation=True)


@run_app.command(name="tunnel")
def run_tunnel(
    target: str = typer.Argument(..., help="port, producer, kafdrop, or databricks"),
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    cap: str | None = typer.Argument(None, help="Cluster type: cap, cap2, or int"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
):
    """Start an SSM tunnel."""
    if target == "producer":
        from ..ssm.tunnel import producer as _old_producer
        _old_producer(env, detach=detach, quiet_deprecation=True)
    elif target == "kafdrop":
        from ..ssm.tunnel import kafdrop as _old_kafdrop
        _old_kafdrop(env, detach=detach, quiet_deprecation=True)
    elif target == "databricks":
        from ..ssm.tunnel import databricks as _old_databricks
        _old_databricks(env, detach=detach, quiet_deprecation=True)
    else:
        if not cap:
            die("cap is required when target is a port number or cluster name")
        from ..ssm.tunnel import connect as _old_connect
        _old_connect(target, env, cap, detach=detach, quiet_deprecation=True)


@run_app.command(name="kafka")
def run_kafka(
    target: str = typer.Argument(..., help="server, ui, or clean"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
):
    """Start local Kafka (server), UI (kafdrop), or clean (reset storage)."""
    from ..kafka.manager import up as _old_kafka_up
    _old_kafka_up(target, detach=detach, quiet_deprecation=True)


@run_app.command(name="workflow")
def run_workflow(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    action: str = typer.Option("run", "--action", "-a", help="run or edit"),
):
    """Run or edit the executor workflow."""
    from ..workflow.debug import executor as _old_executor
    _old_executor(action, env=env, quiet_deprecation=True)
