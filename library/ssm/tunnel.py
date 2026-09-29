import subprocess

import typer
from ..base import BaseCommand
from ..config import Config
from ..deprecation import warn_deprecated
from ..logger import success, info, die

app = typer.Typer(help="SSM port-forwarding tunnels")

CLUSTER_ALIASES = {
    "cap": "nlbcapabilities",
    "cap2": "nlbcapabilities.v2",
    "int": "nlbintegration",
}


class SsmCommand(BaseCommand):
    pass


ssm_cmd = SsmCommand()


@app.command()
def connect(
    ports: str = typer.Argument(..., help="Port(s) to forward (comma/space-separated)"),
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    cluster: str = typer.Argument(..., help="Cluster type: cap, cap2, or int"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Port-forward to a microservice cluster (connectp)."""
    if not quiet_deprecation:
        warn_deprecated("ssm connect", "run tunnel")
    SsmCommand.validate_env(env)
    ssm_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    cluster_host = CLUSTER_ALIASES.get(cluster)
    if not cluster_host:
        die(f"Unknown cluster '{cluster}'. Use: cap, cap2, or int")
    domain = cfg.get("CLUSTER_DOMAIN", "yappycloud.com")
    remote_host = f"{cluster_host}.{env}.{domain}"

    port_list = [int(p) for p in ports.replace(",", " ").split()]

    procs: list[tuple[subprocess.Popen, int]] = []
    for port in port_list:
        info(f"Connecting to {remote_host}:{port}...")
        try:
            proc = ssm_cmd.ssm_tunnel(
                instance=cfg.require("AWS_INSTANCE"),
                port=port,
                local_port=port,
                region=cfg.require("AWS_REGION"),
                profile=cfg.profile,
                remote_host=remote_host,
                quiet=detach,
            )
        except Exception as e:
            die(f"Failed to start tunnel: {e}")
        procs.append((proc, port))
        port_str = f" on localhost:{port}" if detach else ""
        success(f"Tunnel {remote_host}:{port} started (PID {proc.pid}){port_str}")

    if not detach:
        info("All tunnels running. Press Ctrl+C to stop all.")
        for proc, port in procs:
            ssm_cmd.open_browser(f"https://localhost:{port}/swagger-ui/index.html")
        try:
            for proc, _ in procs:
                proc.wait()
        except KeyboardInterrupt:
            for proc, _ in procs:
                proc.terminate()
            ssm_cmd.kill_ssm()
            success("Tunnels closed")
        return

    for proc, port in procs:
        ssm_cmd.serve(
            proc, True,
            name=f"Tunnel {remote_host}:{port}",
            local_port=port,
            browser_url=f"https://localhost:{port}/swagger-ui/index.html",
        )


@app.command()
def producer(
    env: str = typer.Argument(..., help="Environment: dev or qa"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Port-forward to Kafka producer instance (producerp)."""
    if not quiet_deprecation:
        warn_deprecated("ssm producer", "run tunnel producer <env>")
    SsmCommand.validate_env(env)
    ssm_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    info(f"Starting producer tunnel to {env}...")
    try:
        proc = ssm_cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=9091,
            local_port=3000,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            quiet=detach,
        )
    except Exception as e:
        die(f"Failed to start producer tunnel: {e}")

    ssm_cmd.serve(
        proc, detach,
        name="Producer tunnel",
        local_port=3000,
    )


@app.command()
def kafdrop(
    env: str = typer.Argument(..., help="Environment: dev or qa"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Port-forward to Kafdrop UI (up_kafdrop)."""
    if not quiet_deprecation:
        warn_deprecated("ssm kafdrop", "run tunnel kafdrop")
    SsmCommand.validate_env(env)
    ssm_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    local_port = int(cfg.get("KAFDROP_PORT", "9000" if env == "qa" else "9001"))

    info(f"Starting Kafdrop tunnel to {env} (localhost:{local_port})...")
    try:
        proc = ssm_cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=9000,
            local_port=local_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            quiet=detach,
        )
    except Exception as e:
        die(f"Failed to start Kafdrop tunnel: {e}")

    ssm_cmd.serve(
        proc, detach,
        name="Kafdrop tunnel",
        local_port=local_port,
        browser_url=f"http://localhost:{local_port}/",
    )


@app.command()
def databricks(
    env: str = typer.Argument(..., help="Environment: dev or qa"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Port-forward to Databricks workspace (up_databricks)."""
    if not quiet_deprecation:
        warn_deprecated("ssm databricks", "run tunnel databricks")
    SsmCommand.validate_env(env)
    ssm_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    local_port = int(cfg.get("DATABRICKS_PORT", "4433" if env == "dev" else "4434"))
    remote_host = cfg.get(
        "DATABRICKS_HOST",
        f"yappy-lakehouse-{env}.cloud.databricks.com",
    )

    info(f"Starting Databricks tunnel to {env} (localhost:{local_port})...")
    try:
        proc = ssm_cmd.ssm_tunnel(
            instance=cfg.require("AWS_INSTANCE"),
            port=443,
            local_port=local_port,
            region=cfg.require("AWS_REGION"),
            profile=cfg.profile,
            remote_host=remote_host,
            quiet=detach,
        )
    except Exception as e:
        die(f"Failed to start Databricks tunnel: {e}")

    ssm_cmd.serve(
        proc, detach,
        name="Databricks tunnel",
        local_port=local_port,
        browser_url=f"https://localhost:{local_port}/",
    )


@app.command()
def kill(
    all: bool = typer.Option(False, "--all", "-a", help="Kill ALL SSM sessions (legacy global kill)"),
    quiet_deprecation: bool = False,
):
    """Kill active SSM sessions (kill_ssm)."""
    if not quiet_deprecation:
        warn_deprecated("ssm kill", "stop tunnel")
    if all:
        info("Killing all SSM sessions...")
        ssm_cmd.kill_ssm_all()
    else:
        info("Killing tracked SSM sessions...")
        ssm_cmd.kill_ssm()
    success("SSM sessions terminated")
