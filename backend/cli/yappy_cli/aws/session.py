from __future__ import annotations

import getpass
import json
import subprocess

import typer

from yappy_library.adapters.aws_ini import aws_dir, read_ini, write_ini
from yappy_library.adapters.aws_sso import login as sso_login
from yappy_library.adapters.logging import die, info, success
from yappy_library.adapters.processes import BaseCommand, _aws_cmd
from yappy_library.config import Config
from ..deprecation import warn_deprecated

app = typer.Typer(help="AWS session management")
_cfg = Config()


class AwsCommand(BaseCommand):
    pass


aws_cmd = AwsCommand()


def _write_mfa_credentials(profiles: list[str], creds: dict[str, str]):
    """Write MFA credentials directly to ~/.aws, preserving other profiles.

    Avoids `aws configure set aws_secret_access_key <secret>` subprocess calls
    that leak the secret through the process list.
    """
    creds_path = aws_dir() / "credentials"
    config_path = aws_dir() / "config"

    order, sections = read_ini(creds_path)
    for prof in profiles:
        if prof not in sections:
            sections[prof] = {}
            order.append(prof)
        sections[prof]["aws_access_key_id"] = creds["AccessKeyId"]
        sections[prof]["aws_secret_access_key"] = creds["SecretAccessKey"]
        sections[prof]["aws_session_token"] = creds["SessionToken"]
    write_ini(creds_path, order, sections)

    order_cfg, sections_cfg = read_ini(config_path)
    for prof in profiles:
        cfg_name = f"profile {prof}"
        if cfg_name not in sections_cfg:
            sections_cfg[cfg_name] = {}
            order_cfg.append(cfg_name)
    write_ini(config_path, order_cfg, sections_cfg)


@app.command()
def session(
    profile: str | None = None,
    quiet_deprecation: bool = False,
):
    """Login to AWS SSO via browser (replaces 'aws sso login')."""
    if not quiet_deprecation:
        warn_deprecated("aws session", "login aws")
    sso_login(profile or _cfg.profile)


@app.command()
def mfa(
    user: str = typer.Argument(..., help="MFA username"),
    token: str | None = typer.Argument(None, help="MFA token code (prompted if not provided)"),
    quiet_deprecation: bool = False,
):
    """Generate temporary AWS credentials via MFA."""
    if not quiet_deprecation:
        warn_deprecated("aws mfa", "login mfa")
    if not token:
        token = getpass.getpass("MFA code: ")
    aws_cmd.check_requirements("aws")
    base_profile = _cfg.get("AWS_MFA_PROFILE", "base")

    info(f"Getting account ID from profile '{base_profile}'...")
    result = subprocess.run(
        [*_aws_cmd(), "sts", "get-caller-identity",
         "--query", "Account", "--output", "text",
         "--profile", base_profile],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        die(f"Failed to get account: {result.stderr.strip()}")
    account = result.stdout.strip()

    mfa_arn = f"arn:aws:iam::{account}:mfa/{user}"
    info(f"Requesting MFA session for {user} (valid 36h)...")

    result = subprocess.run(
        [*_aws_cmd(), "sts", "get-session-token",
         "--serial-number", mfa_arn,
         "--token-code", token,
         "--duration-seconds", "129600",
         "--profile", base_profile],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        die(f"MFA failed: {result.stderr.strip()}")

    try:
        creds = json.loads(result.stdout)["Credentials"]
    except (json.JSONDecodeError, KeyError):
        die("Failed to parse MFA credentials")

    profiles = [_cfg.profile, "mfa"]
    _write_mfa_credentials(profiles, creds)

    success(f"MFA credentials configured for profiles: {', '.join(profiles)}")
