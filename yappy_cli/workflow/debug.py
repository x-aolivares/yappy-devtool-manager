import importlib.util
import os
import subprocess
import time
from pathlib import Path

import typer

from ..api.kafka import KafkaService
from ..base import BaseCommand, _aws_cmd
from ..config import Config, win_to_posix
from ..db.tunnel import _generate_token, _write_local_env
from ..deprecation import warn_deprecated
from ..logger import info, success, warn, die

app = typer.Typer(help="Composite workflows")


def _default_kafka_agents() -> str:
    """Default kafka-agents location, derived from the environment (no hardcoded Windows path)."""
    return str(
        Path(os.environ.get("SystemDrive", "C:"))
        / "Development" / "Workspace" / "Yappy" / "test" / "kafka-agents"
    )


class WorkflowCommand(BaseCommand):
    def _check_session(self) -> bool:
        cfg = Config()
        result = self.run(
            [*_aws_cmd(), "sts", "get-caller-identity", "--profile", cfg.profile],
            capture=True,
            check=False,
        )
        return result.returncode == 0


wf_cmd = WorkflowCommand()


@app.command(deprecated=True)
def debug_local(
    env: str = typer.Option("dev", "--env", "-e", help="Environment: dev, qa, ..."),
    kafka_agents_path: str = typer.Option(
        None, "--kafka-agents-path", "-k",
        help="Path to kafka-agents project (optional)",
    ),
    quiet_deprecation: bool = False,
):
    """[deprecated] Use 'yappy workflow executor' instead."""
    if not quiet_deprecation:
        warn_deprecated("workflow debug-local", "run workflow")
    WorkflowCommand.validate_env(env)
    wf_cmd.check_requirements("aws")
    cfg = Config.with_env(env)

    info(f"Starting debug workflow for [bold]{env}[/bold]")
    print()
    info("Services that will be started:")
    info(f"  · DB tunnel     -> localhost:{cfg.db_port}")
    info(f"  · Kafka server  -> localhost:9092")
    info(f"  · Kafdrop UI    -> http://localhost:8080")
    info(f"  · Kafka agents  -> {win_to_posix(kafka_agents_path or cfg.get('KAFKA_AGENTS_PATH', _default_kafka_agents()))}")
    print()

    info("[1/5] Checking AWS session...")
    if not wf_cmd._check_session():
        warn("No active AWS session. Run 'yappy aws session' first.")
        yn = input("  Open AWS SSO login now? (y/N): ")
        if yn.lower() == "y":
            from ..aws.session import session as _aws_session
            _aws_session()
            if not wf_cmd._check_session():
                die("AWS SSO login failed. Aborting.")
            success("AWS session established")
        else:
            die("AWS session required. Aborting.")
    else:
        success("AWS session active")

    info("[2/5] Starting database tunnel...")
    token = _generate_token(cfg)
    _write_local_env(token)
    db_proc = wf_cmd.ssm_tunnel(
        instance=cfg.require("AWS_INSTANCE"),
        port=int(cfg.get("AWS_PORT", "53360")),
        local_port=cfg.db_port,
        region=cfg.require("AWS_REGION"),
        profile=cfg.profile,
        remote_host=cfg.require("AWS_HOST"),
    )
    time.sleep(3)
    if db_proc.poll() is None:
        success(f"Database tunnel started (localhost:{cfg.db_port})")
    else:
        warn(f"Database tunnel exited prematurely (code {db_proc.returncode})")

    info("[3/5] Starting local Kafka...")
    kafka_svc = KafkaService(cfg)
    kafka_proc = kafka_svc.up("server", detach=True)

    info("[4/5] Starting Kafdrop UI...")
    ui_proc = kafka_svc.up("ui", detach=True)

    info("[5/5] Checking kafka-agents...")
    agents_path = kafka_agents_path or str(
        Path(cfg.get("KAFKA_AGENTS_PATH", _default_kafka_agents()))
    )
    agents_dir = Path(agents_path)
    if agents_dir.exists():
        success(f"kafka-agents found at {win_to_posix(agents_dir)}")
        info(f"  Start it manually in another terminal:")
        info(f"  cd {win_to_posix(agents_dir)} && <your run command>")
    else:
        warn(f"kafka-agents not found at {agents_dir}")
        info("  You can pass --kafka-agents-path to set the correct path")

    print()
    success("=== Debug workflow initialized ===")
    info(f"  DB tunnel:   localhost:{cfg.db_port}")
    info("  Kafka:       localhost:9092")
    info("  Kafdrop UI:  http://localhost:8080")
    info(f"  Environment: {env}")
    info("  Press Ctrl+C to stop all services")
    print()

    try:
        while True:
            time.sleep(1)
            if db_proc.poll() is not None:
                warn("Database tunnel died. Run 'yappy db up' to restart.")
            if kafka_proc and kafka_proc.poll() is not None:
                warn("Kafka died. Run 'yappy kafka up server' to restart.")
            if ui_proc and ui_proc.poll() is not None:
                warn("Kafdrop UI died. Run 'yappy kafka up ui' to restart.")
    except KeyboardInterrupt:
        info("Shutting down...")
        if ui_proc:
            ui_proc.terminate()
            success("Kafdrop UI stopped")
        kafka_svc.cleanup()
        wf_cmd.kill_ssm()
        success("All services stopped")


_DEFAULT_EXECUTOR = '''\
from yappy_cli.api import Session, DevUtils


def executor(environment: str = "dev") -> tuple:
    session = Session(environment).start()

    db = session.database()
    print(f"DB tunnel ready on localhost:{db.port}")

    cap = session.multiple.pf(ports=[8402, 8403], load_balance="cap")
    cap2 = session.multiple.pf(ports=[8412, 8413], load_balance="cap2")
    bastion = session.bastion.pf(ports=[9091])

    kafka = DevUtils().kafka()
    kafka.up("server")
    kafka.up("ui")

    return session, kafka


if __name__ == "__main__":
    import atexit

    session, kafka = executor()
    atexit.register(lambda: [session.cleanup(), kafka.cleanup()])
'''


def _create_default_executor(path: Path):
    path.write_text(_DEFAULT_EXECUTOR)
    success(f"Created default executor at {path}")


@app.command()
def executor(
    action: str = typer.Argument("run", help="run or edit"),
    env: str = typer.Option("dev", "--env", "-e", help="Environment"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
    quiet_deprecation: bool = False,
):
    """Run or edit the executor script."""
    if not quiet_deprecation:
        warn_deprecated("workflow executor", "run workflow <env>")
    executor_path = Path(__file__).resolve().parent / "executor.py"

    if action == "edit":
        if not executor_path.exists():
            _create_default_executor(executor_path)
        info("Opening executor script in VS Code...")
        subprocess.run(["code", str(executor_path)])
        return

    if action != "run":
        die(f"Unknown action: {action}. Use 'run' or 'edit'.")

    if not executor_path.exists():
        die(f"Executor script not found. Run 'yappy workflow executor edit' to create it first.")

    spec = importlib.util.spec_from_file_location("workflow_executor", executor_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    session, kafka = mod.executor(env)

    if detach:
        success("Executor started in background")
        return

    info("All services running. Press Ctrl+C to stop all.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        info("Shutting down...")
        session.cleanup()
        kafka.cleanup()
        success("All services stopped")
