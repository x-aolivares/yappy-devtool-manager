"""Database authentication and local credential-file adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import botocore.session

from yappy_library.config import Config
from yappy_library.adapters.logging import die, success, warn
from yappy_library.paths import project_root


def generate_token(cfg: Config) -> str:
    """Generate an RDS IAM auth token, falling back to the AWS CLI."""
    try:
        bc_session = botocore.session.Session(profile=cfg.profile)
        rds = bc_session.create_client("rds", region_name=cfg.require("AWS_REGION"))
        token = rds.generate_db_auth_token(
            DBHostname=cfg.require("AWS_HOST"),
            Port=int(cfg.get("AWS_PORT", "53360")),
            DBUsername=cfg.require("AWS_USER"),
            Region=cfg.require("AWS_REGION"),
        )
        if token:
            return token
    except Exception as exc:
        warn(f"botocore token failed, trying fallback: {exc}")

    cmd = [
        sys.executable,
        "-m",
        "awscli",
        "rds",
        "generate-db-auth-token",
        "--hostname",
        cfg.require("AWS_HOST"),
        "--port",
        cfg.get("AWS_PORT", "53360"),
        "--username",
        cfg.require("AWS_USER"),
        "--region",
        cfg.require("AWS_REGION"),
        "--profile",
        cfg.profile,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die(f"Failed to generate DB token: {result.stderr.strip()}")
    return result.stdout.strip()


def _clipboard(text: str) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text, text=True, check=False)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=False)
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text, text=True, check=False
            )
    except Exception:
        pass


def write_local_env(token: str, env_local: Path | None = None) -> None:
    """Persist a generated database token to the project-local env file."""
    if env_local is None:
        env_local = project_root() / "config" / ".env.local"

    try:
        existing = {}
        if env_local.exists():
            for line in env_local.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, value = line.strip().split("=", 1)
                    existing[key] = value
        existing["DB_PASSWORD"] = token
        if "DB_USER" not in existing:
            existing["DB_USER"] = Config().aws_user or ""
        content = "\n".join(f"{key}={value}" for key, value in existing.items()) + "\n"
        env_local.write_text(content)
        if os.name != "nt":
            try:
                os.chmod(env_local, 0o600)
            except OSError:
                pass
        success(f"Token saved to {env_local}")
    except Exception as exc:
        warn(f"Could not write {env_local}: {exc}")

    _clipboard(token)
    success("Token copied to clipboard")
