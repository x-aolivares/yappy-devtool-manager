from __future__ import annotations

import typer

login_app = typer.Typer(help="Authenticate with AWS")


@login_app.command(name="aws")
def login_aws(
    profile: str | None = typer.Option(None, "--profile", "-p", help="AWS Profile"),
):
    """Login to AWS SSO via browser (replaces 'aws sso login')."""
    from ..aws.session import session as _old_session
    _old_session(profile, quiet_deprecation=True)


@login_app.command(name="mfa")
def login_mfa(
    user: str = typer.Argument(..., help="MFA username"),
    token: str = typer.Argument(..., help="MFA token code"),
):
    """Generate temporary AWS credentials via MFA."""
    from ..aws.session import mfa as _old_mfa
    _old_mfa(user, token, quiet_deprecation=True)
