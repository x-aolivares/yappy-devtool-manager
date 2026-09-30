"""`yappy web` — devtool web (API FastAPI + UI Angular).

`yappy web`      -> levanta API + UI juntos (lo normal)
`yappy web api`  -> solo backend
`yappy web ui`   -> solo frontend
`yappy web ca`   -> descarga el bundle de CA de RDS (verificación del server)

La UI se lanza con `node node_modules/@angular/cli/bin/ng.js` en vez de `npx`
o `npm start`: `npx`/`npm` son shims `.cmd` en Windows y su resolución a través
de subprocess es poco fiable, mientras que `ng.js` es un path determinista que
funciona igual en Git Bash, PowerShell y Linux. Es el mismo criterio que usa
`library/base.py::_aws_cmd()` con `python -m awscli`.
"""
from __future__ import annotations

import os
import shutil
import signal
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import typer

from library import process_tracker
from library.logger import console, die, info, success, warn

web_app = typer.Typer(
    help="Run the web devtool (API + frontend)",
    invoke_without_command=True,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _PROJECT_ROOT / "web" / "frontend"
_NG_BIN = _FRONTEND_DIR / "node_modules" / "@angular" / "cli" / "bin" / "ng.js"


# --- helpers -------------------------------------------------------------


def _ports():
    from web.api.ports_registry import YappyPort

    return YappyPort


def _api_command(reload: bool) -> list[str]:
    port = _ports().WEB_API
    cmd = [
        sys.executable, "-m", "uvicorn", "web.api.main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")
    return cmd


def _node_executable() -> str:
    node = shutil.which("node")
    if not node:
        die("node not found on PATH. Install Node.js 20+ to run the web UI.")
    return node


def _ui_command() -> list[str]:
    if not _NG_BIN.exists():
        die(
            f"Angular CLI not installed at {_NG_BIN}.\n"
            f"Run 'cd {_FRONTEND_DIR} && npm install' first."
        )
    port = _ports().WEB_UI_DEV

    # El proxy se genera acá, desde el enum de puertos, en vez de ser un JSON
    # commiteado: un puerto hardcodeado en dos lugares se desincroniza y el
    # síntoma es un 404 en la UI sin explicación.
    from web.api import proxy_config

    proxy_file = proxy_config.write(_FRONTEND_DIR, int(_ports().WEB_API))

    return [
        _node_executable(), str(_NG_BIN),
        "serve",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--proxy-config", str(proxy_file),
    ]


def _kill_tree(pid: int) -> None:
    """Kill a process and its children. `terminate()` is not enough on Windows:
    npm/node spawn a child tree that survives the parent."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/pid", str(pid), "/t", "/f"],
            capture_output=True,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass


def _spawn(cmd: list[str], name: str, cwd: Path | None = None) -> subprocess.Popen:
    info(f"Starting {name}...")
    kwargs: dict = {}
    if sys.platform != "win32":
        # Own session/process group. Without this the child inherits ours and
        # _kill_tree's os.killpg() would take down `yappy web` itself — and the
        # user's shell — along with the server. Harmless on Windows, where
        # _kill_tree uses taskkill /t instead.
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, **kwargs)
    except FileNotFoundError:
        die(f"Command not found: {cmd[0]}")
    # Tracked as resource="web" so `yappy stop web` can clean up if this terminal
    # dies. Deliberately NOT "tunnel"/"service": `kill_ssm()` only matches
    # resource="tunnel", so this can never take an SSM tunnel down with it.
    try:
        process_tracker.track_process(pid=proc.pid, resource="web", target=name)
    except (AttributeError, OSError):
        pass
    return proc


def _stop_all(procs: list[tuple[str, subprocess.Popen]]) -> None:
    for name, proc in procs:
        if proc.poll() is not None:
            continue
        _kill_tree(proc.pid)
        success(f"{name} stopped")
        try:
            process_tracker.untrack_process(proc.pid)
        except (AttributeError, OSError):
            pass


# --- commands ------------------------------------------------------------


@web_app.callback(invoke_without_command=True)
def web(
    ctx: typer.Context,
    no_api: bool = typer.Option(False, "--no-api", help="Don't start the backend"),
    no_ui: bool = typer.Option(False, "--no-ui", help="Don't start the frontend"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="API auto-reload"),
):
    """Start the web devtool: API + UI in one command."""
    if ctx.invoked_subcommand is not None:
        return

    ports = _ports()
    if no_api and no_ui:
        die("Nothing to start: --no-api and --no-ui are mutually exclusive")

    frontend_ready = _NG_BIN.exists()
    if not no_ui and not frontend_ready:
        warn(f"Frontend not scaffolded yet ({_FRONTEND_DIR} missing) — starting API only.")
        warn("See docs/web-app-plan.md; run 'yappy web api' for the API on its own.")

    start_ui = not no_ui and frontend_ready
    if not no_api and start_ui:
        info("")
        info("  API  ->  http://127.0.0.1:%d" % ports.WEB_API)
        info("  Docs ->  http://127.0.0.1:%d/docs" % ports.WEB_API)
        info("  UI   ->  http://127.0.0.1:%d" % ports.WEB_UI_DEV)
        info("")
    elif not no_api:
        info("")
        info("  API  ->  http://127.0.0.1:%d" % ports.WEB_API)
        info("  Docs ->  http://127.0.0.1:%d/docs" % ports.WEB_API)
        info("")

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        if not no_api:
            procs.append(("API", _spawn(_api_command(reload), "API")))
        if start_ui:
            procs.append(("UI", _spawn(_ui_command(), "UI", cwd=_FRONTEND_DIR)))
        if not procs:
            die("Nothing to start")

        info("Press Ctrl+C to stop everything")
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    warn(f"{name} exited (code {code}) — stopping the rest")
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        info("")
        info("Shutting down...")
    finally:
        _stop_all(procs)


@web_app.command(name="api")
def web_api(
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Auto-reload on code changes"),
):
    """Start the FastAPI backend on 127.0.0.1 (localhost only, no auth)."""
    port = _ports().WEB_API
    info(f"Starting Yappy Web API on http://127.0.0.1:{port} (localhost only)...")
    info(f"Docs at http://127.0.0.1:{port}/docs")
    try:
        subprocess.run(_api_command(reload), check=False)
    except FileNotFoundError:
        die("uvicorn not found. Install with: pip install -r docs/requirements-web.txt")
    except KeyboardInterrupt:
        console.print()
        success("Web API stopped")


@web_app.command(name="ui")
def web_ui(
    open_browser: bool = typer.Option(False, "--open", help="Open the browser on start"),
):
    """Start the Angular dev server on 127.0.0.1 (localhost only)."""
    port = _ports().WEB_UI_DEV
    if not _FRONTEND_DIR.exists():
        die(f"Frontend not scaffolded yet: {_FRONTEND_DIR} does not exist")
    info(f"Starting Yappy Web UI on http://127.0.0.1:{port}...")
    cmd = _ui_command()
    if open_browser:
        cmd.append("--open")
    try:
        subprocess.run(cmd, cwd=str(_FRONTEND_DIR), check=False)
    except FileNotFoundError:
        die("node not found on PATH. Install Node.js 20+")
    except KeyboardInterrupt:
        console.print()
        success("Web UI stopped")


@web_app.command(name="ca")
def web_ca():
    """Download the RDS CA bundle so connections can verify the server cert.

    RDS requires TLS for IAM auth (the token is the password), but the
    certificate is not publicly verifiable — it chains to a private Amazon root.
    Without the bundle the web still connects, encrypted but unverified; this
    command is what upgrades it to full validation.
    """
    from web.api.infrastructure import mysql_connection as mc

    target = mc.ca_install_path()
    console.print(f"[dim]Downloading AWS RDS root CAs...[/dim]")
    try:
        with urllib.request.urlopen(mc.CA_URL, timeout=30) as resp:
            data = resp.read()
    except Exception as e:  # noqa: BLE001 - network failure, any source
        die(f"Could not download the RDS CA bundle from {mc.CA_URL}: {e}")

    # Trust anchor material: if this file were malformed, every subsequent
    # connection would fail in a way that looks like a server fault. Parse it
    # before writing it.
    if not data or b"BEGIN CERTIFICATE" not in data:
        die("The downloaded file does not look like a PEM bundle. Aborting.")
    try:
        # A bare context, NOT create_default_context(): that one also loads the
        # system trust store, so the count below would include the OS's CAs and
        # report a bundle we never downloaded.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=data.decode())
        count = len(ctx.get_ca_certs())
    except (ssl.SSLError, ValueError, UnicodeDecodeError) as e:
        die(f"The downloaded bundle is not valid PEM: {e}")
    if count == 0:
        die("The downloaded bundle parsed to zero certificates. Aborting.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    success(f"RDS CA bundle saved to {target} ({count} certificates)")
    info("Environment connections will now verify the RDS certificate.")
