from __future__ import annotations

import subprocess
import sys

import typer

from ..logger import info, die

exec_app = typer.Typer(help="Execute commands in environment context")


@exec_app.command(name="aws")
def exec_aws(
    env: str = typer.Argument(..., help="Environment: dev, qa, ..."),
    command: list[str] = typer.Argument(
        ..., help="AWS CLI subcommand (e.g. s3 ls)"
    ),
):
    """Execute AWS CLI using the profile and region of the environment."""
    from ..base import _aws_cmd
    from ..config import Config
    cfg = Config.with_env(env)
    profile = cfg.profile
    region = cfg.region
    info(f"Executing AWS CLI (profile={profile}, region={region})")
    aws_invocation = [*_aws_cmd(), "--profile", profile, "--region", region]
    full_cmd = aws_invocation + command
    info(f"$ {' '.join(full_cmd)}")
    try:
        proc = subprocess.run(full_cmd, check=False)
    except FileNotFoundError:
        die("AWS CLI not found.")
    sys.exit(proc.returncode)
