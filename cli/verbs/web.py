"""`yappy web` — devtool web (API FastAPI + UI Angular).

`yappy web`      -> levanta API + UI juntos (lo normal)
`yappy web api`  -> solo backend
`yappy web ui`   -> solo frontend
`yappy web ca`   -> descarga el bundle de CA de RDS (verificación del server)

Los puertos salen de `web/api/ports_registry.py`. Si el que corresponde está
tomado o reservado, `yappy web` **no falla**: busca el siguiente libre, lo usa y
avisa cuál tomó y quién tenía el anterior. La variable de entorno no es un
requisito — es de dónde arranca la búsqueda:

    YAPPY_WEB_API_PORT=8399 yappy web
    YAPPY_WEB_UI_PORT=4399  yappy web

Los puertos de DB son lo contrario: están pinneados en `config/env.*` porque
DBeaver se conecta a ellos explícitamente, así que moverlos sería un bug y no
una comodidad. Por eso esta tolerancia vive acá y no en `ports_registry`.

Antes de spawnear se hace un bind de prueba. En Windows eso importa: un puerto
reservado por Hyper-V/WSL2/Docker falla con `WinError 10013` (WSAEACCES), que
parece un problema de permisos y no de puertos, y uvicorn no da ninguna pista de
qué hacer al respecto. Al buscar el siguiente libre, esos rangos reservados se
saltan solos: el bind falla igual y el escaneo sigue.

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
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import typer

from library import process_tracker
from library.logger import console, die, info, success, warn

# `ports_registry` is stdlib-only, so the top-level import costs nothing and
# keeps the env var names in one place instead of duplicated string literals.
from web.api.ports_registry import (
    ENV_WEB_API_PORT,
    ENV_WEB_UI_PORT,
    web_api_port,
    web_ui_port,
)

web_app = typer.Typer(
    help="Run the web devtool (API + frontend)",
    invoke_without_command=True,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _PROJECT_ROOT / "web" / "frontend"
_NG_BIN = _FRONTEND_DIR / "node_modules" / "@angular" / "cli" / "bin" / "ng.js"


# --- helpers -------------------------------------------------------------


def _preferred_api_port() -> int:
    """API port to aim for (override or enum default).

    "Preferred" and not "the port": `_resolve_port` may move it if it is taken.
    Dies on a bad override — silently using the default would bind a port the
    user explicitly asked to change.
    """
    try:
        return web_api_port()
    except ValueError as e:
        die(str(e))


def _preferred_ui_port() -> int:
    """UI port to aim for. See `_preferred_api_port`."""
    try:
        return web_ui_port()
    except ValueError as e:
        die(str(e))


def _parse_netstat_listeners(output: str, port: int) -> int | None:
    """Pull the PID LISTENING on `port` out of `netstat -ano -p TCP` output.

    Split out from the subprocess call so the parsing — which is the part that
    can quietly get a column wrong — is testable without a Windows box.
    """
    for line in output.splitlines():
        parts = line.split()
        # TCP  <local>  <remote>  LISTENING  <pid>
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[1].rsplit(":", 1)[-1] != str(port) or not parts[4].isdigit():
            continue
        return int(parts[4])
    return None


def _pid_listening_on(port: int) -> int | None:
    """Which PID is LISTENING on this TCP port, if the OS will tell us.

    Best effort by design: this only feeds a better error message, so any
    failure returns None instead of getting in the way of the actual startup.
    """
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_netstat_listeners(out, port)


def _process_name(pid: int) -> str:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "?"
    # "python.exe","12345","Console","1","123.456 K"
    for line in out.splitlines():
        cells = [c.strip('" ') for c in line.split('","')]
        if len(cells) >= 2 and cells[1] == str(pid):
            return cells[0]
    return "?"


def _tracked_web_pids() -> set[int]:
    """PIDs yappy recorded for resource="web" that are still alive."""
    try:
        from library import process_tracker

        return {
            p["pid"]
            for p in process_tracker.get_tracked_processes(resource="web")
            if p.get("alive")
        }
    except Exception:  # noqa: BLE001 - only used to enrich an error message
        return set()


def _try_bind(port: int) -> OSError | None:
    """Try to bind 127.0.0.1:`port`. Returns the OSError, or None on success."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Without this, a port held by another process can still look
            # bindable while the server is actually listening.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
    except OSError as e:
        return e
    finally:
        sock.close()
    return None


def _port_is_free(port: int) -> bool:
    """Whether 127.0.0.1:`port` can be bound right now. Never raises."""
    return _try_bind(port) is None


#: How many ports to try past the preferred one before giving up. A collision is
#: normally a port or two away; scanning hundreds would only delay the error.
_PORT_SCAN_LIMIT = 50


def _find_free_port(start: int) -> int | None:
    """First bindable port at or after `start`, or None if the scan runs out."""
    last = min(start + _PORT_SCAN_LIMIT, 65535)
    for port in range(max(start, 1), last + 1):
        if _port_is_free(port):
            return port
    return None


def _owner_phrase(port: int) -> str:
    """Who holds the port, phrased to fit inside a sentence."""
    owner = _describe_owner(port)
    if owner is None:
        return "held by another process"
    pid, name, ours = owner
    if ours:
        return f"a leftover of yours: {name} (PID {pid}) — 'yappy stop web' cleans it up"
    return f"held by {name} (PID {pid}), which is not a yappy process"


def _resolve_port(preferred: int, label: str, override_var: str) -> int:
    """The port to actually use: `preferred`, or the next free one.

    `yappy web` must not be blocked by whatever sits on the port — the classic
    case being the child of a dead `uvicorn --reload`, which keeps the listening
    socket after its parent is gone. That leniency lives here and only here: DB
    ports stay pinned to `config/env.*` because DBeaver connects to them
    explicitly, so moving one behind the user's back would be a bug.

    A collision is never silent — the owner is named, so it is obvious whether
    it is a leftover of yours or something foreign.
    """
    error = _try_bind(preferred)
    if error is None:
        return preferred

    chosen = _find_free_port(preferred + 1)
    if chosen is None:
        # Nothing nearby either: this is a real failure, so report why the
        # preferred port could not be taken (10013 and 10048 need different fixes).
        die(_port_error(preferred, label, override_var, error))

    warn(f"Port {preferred} for {label} is taken — {_owner_phrase(preferred)}.")
    warn(f"Using {chosen} instead.")
    return chosen


def _port_error(port: int, label: str, override_var: str, e: OSError) -> str:
    """Translate a bind failure into the command that actually fixes it."""
    # `winerror` only exists on Windows sockets; on other platforms the OSError
    # has no such attribute and reading it would break the error handler.
    code = getattr(e, "winerror", None) or e.errno
    head = f"Cannot bind 127.0.0.1:{port} for {label} ({e})."
    escape = (
        f"Or start the port search somewhere else:\n"
        f"  {override_var}=<otro> yappy web"
    )

    if sys.platform == "win32" and code == 10013:
        return (
            f"{head}\n"
            f"On Windows this is almost never a permissions problem: the port is\n"
            f"reserved by Hyper-V / WSL2 / Docker, or blocked by the firewall.\n"
            f"See the reserved ranges with:\n"
            f"  netsh interface ipv4 show excludedportrange protocol=tcp\n"
            f"Easiest way out — pick a free port for this run:\n"
            f"  {override_var}=<otro> yappy web"
        )

    if code in (10048, 98):  # WSAEADDRINUSE / EADDRINUSE
        owner = _describe_owner(port)
        if owner is None:
            return (
                f"{head}\n"
                f"Another process is already listening on that port.\n"
                f"  yappy stop web        # if it is a leftover of yours\n"
                f"  netstat -ano | findstr :{port}\n"
                f"{escape}"
            )
        pid, name, ours = owner
        if ours:
            return (
                f"{head}\n"
                f"Port {port} is held by a leftover of yours: {name} (PID {pid}).\n"
                f"  yappy stop web        # cleans it up, children included\n"
                f"{escape}"
            )
        return (
            f"{head}\n"
            f"Port {port} is held by {name} (PID {pid}), which is not a tracked\n"
            f"yappy process — so 'yappy stop web' will not touch it. If it is a\n"
            f"previous session of yours, kill it with:\n"
            f"  taskkill /PID {pid} /T /F\n"
            f"{escape}"
        )

    return f"{head}\n{escape}"


def _describe_owner(port: int) -> tuple[int, str, bool] | None:
    """(pid, image name, is-ours) for whatever holds the port, or None."""
    pid = _pid_listening_on(port)
    if pid is None:
        return None
    return pid, _process_name(pid), pid in _tracked_web_pids()


def _api_command(port: int, reload: bool) -> list[str]:
    """The uvicorn command for an already-resolved port.

    The port is a parameter, not read from the env here, so that the value
    uvicorn binds and the value the UI proxy points at can never drift apart.
    """
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


def _ui_command(api_port: int, ui_port: int) -> list[str]:
    """The `ng serve` command. Both ports arrive already resolved."""
    if not _NG_BIN.exists():
        die(
            f"Angular CLI not installed at {_NG_BIN}.\n"
            f"Run 'cd {_FRONTEND_DIR} && npm install' first."
        )

    # El proxy se genera acá, desde el puerto resuelto, en vez de ser un JSON
    # commiteado: un puerto hardcodeado en dos lugares se desincroniza y el
    # síntoma es un 404 en la UI sin explicación.
    from web.api import proxy_config

    proxy_file = proxy_config.write(_FRONTEND_DIR, api_port)

    return [
        _node_executable(), str(_NG_BIN),
        "serve",
        "--host", "127.0.0.1",
        "--port", str(ui_port),
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

    if no_api and no_ui:
        die("Nothing to start: --no-api and --no-ui are mutually exclusive")

    frontend_ready = _NG_BIN.exists()
    if not no_ui and not frontend_ready:
        warn(f"Frontend not scaffolded yet ({_FRONTEND_DIR} missing) — starting API only.")
        warn("See docs/web-app-plan.md; run 'yappy web api' for the API on its own.")

    start_ui = not no_ui and frontend_ready

    # La API se resuelve siempre, incluso con --no-api: el proxy de la UI tiene
    # que apuntarle, y si la API no la levanta este comando es porque el usuario
    # la corre aparte (`yappy web api`) apuntando al puerto preferido.
    api_port = (
        _resolve_port(_preferred_api_port(), "the API", ENV_WEB_API_PORT)
        if not no_api
        else _preferred_api_port()
    )
    ui_port = (
        _resolve_port(_preferred_ui_port(), "the UI", ENV_WEB_UI_PORT)
        if start_ui
        else _preferred_ui_port()
    )

    if not no_api and start_ui:
        info("")
        info("  API  ->  http://127.0.0.1:%d" % api_port)
        info("  Docs ->  http://127.0.0.1:%d/docs" % api_port)
        info("  UI   ->  http://127.0.0.1:%d" % ui_port)
        info("")
    elif not no_api:
        info("")
        info("  API  ->  http://127.0.0.1:%d" % api_port)
        info("  Docs ->  http://127.0.0.1:%d/docs" % api_port)
        info("")

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        if not no_api:
            procs.append(("API", _spawn(_api_command(api_port, reload), "API")))
        if start_ui:
            procs.append(("UI", _spawn(_ui_command(api_port, ui_port), "UI", cwd=_FRONTEND_DIR)))
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
    port = _resolve_port(_preferred_api_port(), "the API", ENV_WEB_API_PORT)
    info(f"Starting Yappy Web API on http://127.0.0.1:{port} (localhost only)...")
    info(f"Docs at http://127.0.0.1:{port}/docs")
    try:
        subprocess.run(_api_command(port, reload), check=False)
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
    if not _FRONTEND_DIR.exists():
        die(f"Frontend not scaffolded yet: {_FRONTEND_DIR} does not exist")
    ui_port = _resolve_port(_preferred_ui_port(), "the UI", ENV_WEB_UI_PORT)
    info(f"Starting Yappy Web UI on http://127.0.0.1:{ui_port}...")
    cmd = _ui_command(_preferred_api_port(), ui_port)
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
