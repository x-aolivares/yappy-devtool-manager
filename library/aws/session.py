from __future__ import annotations

import getpass
import hashlib
import json
import os
import subprocess
import time
import webbrowser
from pathlib import Path

import botocore.session
import typer
from botocore import UNSIGNED
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from ..base import BaseCommand, _aws_cmd
from ..config import Config
from ..deprecation import warn_deprecated
from ..logger import success, info, die

app = typer.Typer(help="AWS session management")
_cfg = Config()


class AwsCommand(BaseCommand):
    pass


aws_cmd = AwsCommand()


def _parse_aws_config() -> dict[str, dict[str, str]]:
    path = Path.home() / ".aws" / "config"
    if not path.exists():
        die("~/.aws/config not found")
    config: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            config[section] = {}
        elif "=" in line and section is not None:
            k, v = line.split("=", 1)
            config[section][k.strip()] = v.strip()
    return config


def _read_ini(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Return (section_order, sections) from a simple INI file."""
    order: list[str] = []
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    if path.exists():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                if current not in sections:
                    sections[current] = {}
                    order.append(current)
            elif (
                current is not None
                and "=" in stripped
                and not stripped.startswith(("#", ";"))
            ):
                k, v = stripped.split("=", 1)
                sections[current][k.strip()] = v.strip()
    return order, sections


def _write_ini(path: Path, order: list[str], sections: dict[str, dict[str, str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in order:
        lines.append(f"[{name}]")
        for k, v in sections[name].items():
            lines.append(f"{k} = {v}")
    path.write_text("\n".join(lines) + "\n")
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _write_mfa_credentials(profiles: list[str], creds: dict[str, str]):
    """Write MFA credentials directly to ~/.aws, preserving other profiles.

    Avoids `aws configure set aws_secret_access_key <secret>` subprocess calls
    that leak the secret through the process list.
    """
    home = Path.home()
    creds_path = home / ".aws" / "credentials"
    config_path = home / ".aws" / "config"

    order, sections = _read_ini(creds_path)
    for prof in profiles:
        if prof not in sections:
            sections[prof] = {}
            order.append(prof)
        sections[prof]["aws_access_key_id"] = creds["AccessKeyId"]
        sections[prof]["aws_secret_access_key"] = creds["SecretAccessKey"]
        sections[prof]["aws_session_token"] = creds["SessionToken"]
    _write_ini(creds_path, order, sections)

    order_cfg, sections_cfg = _read_ini(config_path)
    for prof in profiles:
        cfg_name = f"profile {prof}"
        if cfg_name not in sections_cfg:
            sections_cfg[cfg_name] = {}
            order_cfg.append(cfg_name)
    _write_ini(config_path, order_cfg, sections_cfg)


def _sso_oidc_client(region: str):
    """Create a botocore SSO-OIDC client without triggering credential_process."""
    saved = {}
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SHARED_CREDENTIALS_FILE"):
        if var in os.environ:
            saved[var] = os.environ.pop(var)
    try:
        bc_session = botocore.session.Session()
        return bc_session.create_client(
            "sso-oidc",
            region_name=region,
            config=BotoConfig(signature_version=UNSIGNED),
        )
    finally:
        os.environ.update(saved)


def _sso_cache_path(session_name: str) -> Path:
    key = hashlib.sha1(session_name.encode("utf-8")).hexdigest()
    path = Path.home() / ".aws" / "sso" / "cache" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@app.command()
def session(
    profile: str | None = None,
    quiet_deprecation: bool = False,
):
    """Login to AWS SSO via browser (replaces 'aws sso login')."""
    if not quiet_deprecation:
        warn_deprecated("aws session", "login aws")
    cfg_profile = profile or _cfg.profile
    config = _parse_aws_config()

    profile_key = f"profile {cfg_profile}"
    if profile_key not in config:
        die(f"Profile '{cfg_profile}' not found in ~/.aws/config")

    prof = config[profile_key]
    sso_session_name = prof.get("sso_session")
    if not sso_session_name:
        die(f"No sso_session configured for profile '{cfg_profile}'")

    session_key = f"sso-session {sso_session_name}"
    if session_key not in config:
        die(f"SSO session '{sso_session_name}' not found in ~/.aws/config")

    sso_cfg = config[session_key]
    start_url = sso_cfg.get("sso_start_url", "")
    sso_region = sso_cfg.get("sso_region", "us-east-1")

    info(f"SSO start URL: {start_url}")
    info(f"SSO region: {sso_region}")

    client = _sso_oidc_client(sso_region)

    # 1 — Register OAuth client
    info("Registering OAuth client with AWS SSO...")
    reg = client.register_client(clientName="yappy-cli", clientType="public")
    client_id = reg["clientId"]
    client_secret = reg["clientSecret"]

    # 2 — Device authorization
    auth = client.start_device_authorization(
        clientId=client_id,
        clientSecret=client_secret,
        startUrl=start_url,
    )

    # 3 — Open browser
    info("Opening browser for SSO login...")
    webbrowser.open(auth["verificationUriComplete"])
    info(f"Your device code: [bold]{auth['userCode']}[/bold]")
    info("If the browser doesn't open, visit:")
    info(f"  {auth['verificationUriComplete']}")

    # 4 — Poll for token
    interval = auth.get("interval", 5)
    info(f"Waiting for authorization (polling every {interval}s)...")
    while True:
        try:
            token = client.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=auth["deviceCode"],
            )
            break
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "AuthorizationPendingException":
                time.sleep(interval)
            else:
                die(f"SSO authorization failed: {e}")

    # 5 — Cache the token (AWS CLI v2 compatible format)
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + token["expiresIn"]),
    )
    cache = {
        "startUrl": start_url,
        "region": sso_region,
        "accessToken": token["accessToken"],
        "expiresAt": expires_at,
        "clientId": client_id,
        "clientSecret": client_secret,
        "registrationExpiresAt": expires_at,
    }
    cache_path = _sso_cache_path(sso_session_name)
    cache_path.write_text(json.dumps(cache, indent=2))

    success(f"SSO session established (expires: {expires_at})")
    success(f"Token cached: {cache_path}")


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
