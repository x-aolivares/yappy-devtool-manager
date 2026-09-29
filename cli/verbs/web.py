from __future__ import annotations

import subprocess
import sys

import typer

from library.logger import info, success, die

web_app = typer.Typer(help="Run the web devtool (API + frontend)")


@web_app.command(name="api")
def web_api(
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Auto-reload on code changes"),
):
    """Start the FastAPI backend on 127.0.0.1 (localhost only, no auth)."""
    from web.api.ports_registry import YappyPort

    port = int(YappyPort.WEB_API)
    info(f"Starting Yappy Web API on http://127.0.0.1:{port} (localhost only)...")
    cmd = [
        sys.executable, "-m", "uvicorn", "web.api.main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        die("uvicorn not found. Install with: pip install -r docs/requirements-web.txt")
    except KeyboardInterrupt:
        success("Web API stopped")
