import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from yappy_library.adapters.logging import console, die, info, success, warn
from yappy_library.config import Config, win_to_posix
from yappy_library.paths import (
    migrate_backend_resources,
    project_config_dir,
    project_root as get_project_root,
)
from .aws.session import app as aws_app
from .db.tunnel import app as db_app
from .ssm.tunnel import CLUSTER_ALIASES, app as ssm_app
from .kafka.manager import app as kafka_app
from .workflow.debug import app as workflow_app
from .verbs.run import run_app
from .verbs.stop import stop_app
from .verbs.login import login_app
from .verbs.exec import exec_app
from .verbs.logs import logs_app


app = typer.Typer(
    name="yappy",
    help="AWS CLI Manager - orchestrate AWS, DB, Kafka, and SSM workflows",
    no_args_is_help=True,
)

# Viejos (deprecated)
app.add_typer(aws_app, name="aws", help="AWS session management [deprecated]")
app.add_typer(db_app, name="db", help="Database tunnel management [deprecated]")
app.add_typer(ssm_app, name="ssm", help="SSM tunnels [deprecated]")
app.add_typer(kafka_app, name="kafka", help="Local Kafka [deprecated]")
app.add_typer(workflow_app, name="workflow", help="Workflows [deprecated]")

# Nuevos (Docker-like)
app.add_typer(run_app, name="run", help="Start a resource")
app.add_typer(stop_app, name="stop", help="Stop a resource")
app.add_typer(login_app, name="login", help="Authenticate with AWS")
app.add_typer(exec_app, name="exec", help="Execute commands in environment context")
app.add_typer(logs_app, name="logs", help="Show logs of managed processes")


_FALLBACK_VERSION = "0.11.0"


def _read_version() -> str:
    project_root = get_project_root()
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return _FALLBACK_VERSION


@app.command()
def version():
    """Show the installed version."""
    from importlib.metadata import version as _v
    try:
        ver = _v("yappy-cli-manager")
    except Exception:
        ver = _read_version()
    info(f"yappy-cli-manager v{ver}")


@app.command()
def config(env: str = typer.Argument(None, help="Environment to show (dev, qa, ...)")):
    """Show configuration for one or all environments."""
    known = Config.known_environments()
    envs = [env] if env else known

    for e in envs:
        try:
            cfg = Config.with_env(e)
        except ValueError:
            info(f"No config found for '{e}'")
            continue
        if env and len(envs) > 1:
            print()
        info(f"[bold]=== {e.upper()} ===[/bold]")
        info(f"  AWS Profile:  {cfg.profile}")
        info(f"  AWS Region:   {cfg.region}")
        info(f"  AWS Instance: {cfg.instance or '(not set)'}")
        info(f"  AWS Host:     {cfg.host or '(not set)'}")
        info(f"  AWS Cluster:  {cfg.cluster or '(not set)'}")
        info(f"  DB Port:      {cfg.db_port}")

    if not env:
        base = Config()
        print()
        info("[bold]=== BASE ===[/bold]")
        info(f"  AWS User:     {base.aws_user}")
        info(f"  Kafka Path:   {win_to_posix(base.kafka_path)}")
        info(f"  Profile:      {win_to_posix(base.profile_path)}")
        info(f"  Workspace:    {win_to_posix(base.workspace_path)}")


@app.command()
def workspace():
    """Show the project workspace path."""
    cfg = Config()
    print(win_to_posix(cfg.workspace_path))


@app.command()
def home():
    """Show the yappy project root path."""
    print(win_to_posix(str(get_project_root())))


@app.command()
def web(
    port: int = typer.Option(8000, "--port", "-p", help="Puerto de la web"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="No abrir el navegador automáticamente"
    ),
    build: bool | None = typer.Option(
        None,
        "--build/--no-build",
        help="Compilar el frontend Angular. Por defecto se compila "
        "solo si falta dist/browser o el código está desactualizado.",
    ),
):
    """Abrir la web de Region Sync (diff de DB y de parámetros/secretos entre ambientes)."""
    if build or (build is None and _frontend_needs_build()):
        _build_web_frontend()
    from yappy_api.run import run

    run(port=port, open_browser=not no_browser)


def _frontend_needs_build(frontend: Path | None = None) -> bool:
    """True si el frontend dist/browser falta o sus fuentes son más nuevas."""
    frontend = frontend or (get_project_root() / "frontend")
    if not (frontend / "package.json").exists():
        return False
    index = frontend / "dist" / "browser" / "index.html"
    if not index.exists():
        return True
    if not shutil.which("npm"):
        return False
    source_files: list[Path] = [
        frontend / "package.json",
        frontend / "angular.json",
        frontend / "package-lock.json",
        frontend / "node_modules" / ".package-lock.json",
    ]
    for cfg in frontend.glob("tsconfig*.json"):
        source_files.append(cfg)
    src = frontend / "src"
    if src.exists():
        source_files.append(src)
    newest = 0.0
    for source in source_files:
        if not source.exists():
            continue
        try:
            if source.is_dir():
                newest = max(
                    newest,
                    max(p.stat().st_mtime for p in source.rglob("*") if p.is_file()),
                )
            else:
                newest = max(newest, source.stat().st_mtime)
        except OSError:
            continue
    return newest > index.stat().st_mtime


def _build_web_frontend() -> None:
    """Compile the Angular frontend (frontend/ dist) on demand."""
    frontend = get_project_root() / "frontend"
    npm = shutil.which("npm")
    if not npm:
        die("npm no está instalado — instalá Node.js (>=24.15) para compilar el frontend")
    if not (frontend / "package.json").exists():
        die(f"No se encontró frontend/ en {frontend}")
    if not (frontend / "node_modules").exists():
        die(
            "Faltan las dependencias del frontend (frontend/node_modules no existe).\n"
            "  Ejecuta 'yappy setup' (o 'cd frontend && npm ci') y volve a intentar."
        )
    info("Compilando frontend Angular (frontend/ -> dist/browser)...")
    result = subprocess.run(
        [npm, "run", "build"],
        cwd=str(frontend),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        die(f"Falló el build del frontend:\n{result.stdout}\n{result.stderr}")
    success("Frontend compilado.")


def _install_backend_deps(project_root: Path) -> None:
    """Editable-install the package so Python deps stay in sync.

    pyproject.toml declares dependencies as dynamic, read from
    docs/requirements.txt at build time. Re-running this picks up deps added
    there since the last install (that file is a shared stack synced across
    repos). On the very first run it is a no-op: install.sh already did it.
    """
    if not (project_root / "pyproject.toml").exists():
        warn(f"  pyproject.toml not found at {project_root} - skipping backend install")
        return

    info("  Installing backend deps (python -m pip install -e .)...")
    # sys.executable, not bare `pip`: guarantees the same interpreter that runs
    # yappy, so deps and the CLI entry point never land in different envs.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(project_root)],
        check=False,
    )
    if result.returncode == 0:
        success("  Backend (yappy-cli-manager) installed in editable mode")
    else:
        warn(
            f"  Backend install failed (exit {result.returncode}) - see the pip output above"
        )


def _install_frontend_deps(frontend: Path) -> None:
    """Install frontend/node_modules with `npm install`.

    Always runs: npm install is idempotent, resolves from package.json, and
    self-heals drift between package.json, package-lock.json and node_modules.
    """
    if not (frontend / "package.json").exists():
        warn(f"  frontend/package.json not found at {frontend} - skipping")
        return

    npm = shutil.which("npm")
    if not npm:
        warn("  npm not found - install Node.js (>=24.15) to build the frontend")
        return

    info("  Installing frontend deps (npm install) - this may take a few minutes...")
    result = subprocess.run([npm, "install"], cwd=str(frontend), check=False)
    if result.returncode == 0:
        success("  Frontend deps installed (npm install)")
    else:
        warn(f"  Frontend install failed (npm install, exit {result.returncode})")


@app.command()
def init(shell: str = typer.Argument("bash", help="Shell type: bash, zsh, powershell")):
    """Generate shell integration — add to .bashrc: eval "$(yappy init bash)"."""
    if shell == "powershell":
        print(r"""function yappy {
  $script:YappyExe = (Get-Command yappy -CommandType Application).Source
  if ($args[0] -eq "workspace") { Set-Location (& $script:YappyExe workspace) }
  elseif ($args[0] -eq "home") { Set-Location (& $script:YappyExe home) }
  elseif ($args[0] -eq "reload") { & $script:YappyExe reload; . $PROFILE }
  else { & $script:YappyExe @args }
}""")
        return

    print(r"""# yappy shell integration
yappy() {
  if [ "$1" = "workspace" ]; then
    cd "$(command yappy workspace)"
  elif [ "$1" = "home" ]; then
    cd "$(command yappy home)"
  elif [ "$1" = "reload" ]; then
    command yappy reload && source ~/.bashrc
  else
    command yappy "$@"
  fi
}

# yappy bash completions
_yappy_completions() {
  local cur prev words cword
  _init_completion 2>/dev/null || { cur="${COMP_WORDS[COMP_CWORD]}"; prev="${COMP_WORDS[COMP_CWORD-1]}"; }

  local top_cmds="aws db ssm kafka workflow run stop login exec logs version config workspace home init reload setup edit update ps py-purge uninstall"

  case "${COMP_WORDS[1]}" in
    aws)      COMPREPLY=($(compgen -W "session mfa" -- "$cur")) ;;
    db)       COMPREPLY=($(compgen -W "up refresh" -- "$cur")) ;;
    ssm)      COMPREPLY=($(compgen -W "connect producer kafdrop databricks kill" -- "$cur")) ;;
    kafka)    COMPREPLY=($(compgen -W "up down" -- "$cur")) ;;
    workflow)  COMPREPLY=($(compgen -W "debug-local executor" -- "$cur")) ;;
    run)      COMPREPLY=($(compgen -W "db tunnel kafka workflow" -- "$cur")) ;;
    stop)     COMPREPLY=($(compgen -W "kafka tunnel" -- "$cur")) ;;
    login)    COMPREPLY=($(compgen -W "aws mfa" -- "$cur")) ;;
    exec)     COMPREPLY=($(compgen -W "aws" -- "$cur")) ;;
    logs)     COMPREPLY=($(compgen -W "db kafka tunnel" -- "$cur")) ;;
    config)   COMPREPLY=($(compgen -W "$(command yappy config 2>/dev/null | grep -oP '(?<===\s)\w+' | tr '[:upper:]' '[:lower:]')" -- "$cur")) ;;
    *)        COMPREPLY=($(compgen -W "$top_cmds" -- "$cur")) ;;
  esac
}
complete -F _yappy_completions yappy""")


@app.command()
def reload():
    """Reinstall the package in editable mode (pip install -e .)."""
    project_root = get_project_root()
    pyproject = project_root / "pyproject.toml"
    version_str = "unknown"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.strip().startswith('version'):
                version_str = line.split("=")[1].strip().strip('"').strip("'")
                break
    info(f"Reinstalling Yappy-ToolKit v{version_str} from {project_root}...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(project_root)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        success(f"Yappy-ToolKit v{version_str} reinstalled successfully")
    else:
        die(f"Reinstall failed: {result.stderr.strip()}")


def _parse_ssm_info(cmdline: str) -> tuple[str, str]:
    env = os.environ.get("AWS_ENVIRONMENT") or ""
    if not env:
        prof_m = re.search(r'--profile\s+(\S+)', cmdline)
        env = prof_m.group(1).split("-")[-1] if prof_m else "?"

    if "AWS-StartPortForwardingSessionToRemoteHost" not in cmdline:
        return "Bastion", env

    host_m = re.search(r'"host":\["([^"]+)"\]', cmdline)
    if not host_m:
        return "Port Forward", env
    host = host_m.group(1)

    host_parts = host.split(".")
    if len(host_parts) >= 3:
        env = host_parts[1]

    for alias, prefix in sorted(CLUSTER_ALIASES.items(), key=lambda x: -len(x[1])):
        if host.startswith(prefix + "."):
            return f"SSM Tunnel - {alias}", env

    if "rds" in host:
        return "DB Tunnel", env

    return f"SSM Tunnel - {host_parts[0]}", env


@app.command()
def ps():
    """List active processes started by Yappy-ToolKit."""
    from rich.table import Table
    from rich import box

    rows = []
    known_pids = set()
    port_map = {}

    try:
        netstat = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
        )
        for line in netstat.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[0] == "TCP" and "127.0.0.1:" in parts[1]:
                port = parts[1].split(":")[1]
                pid = parts[-1]
                port_map.setdefault(pid, []).append(port)
    except Exception:
        pass

    # AWS CLI processes running SSM sessions (has full command line with target info)
    try:
        ps_cmd = [
            "powershell", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='aws.exe' AND CommandLine LIKE '%ssm start-session%'\""
            " | ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }",
        ]
        result = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().splitlines():
            if "|" not in line:
                continue
            pid, cmdline = line.split("|", 1)
            pid = pid.strip()
            if not pid.isdigit() or pid in known_pids:
                continue
            known_pids.add(pid)
            label, env = _parse_ssm_info(cmdline)
            for p in (port_map.get(pid, ["?"])):
                rows.append((pid, env, label, p))
    except Exception:
        pass

    # session-manager-plugin.exe (orphan tunnels where CLI already exited)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq session-manager-plugin.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.strip('"').split('","')
            if len(parts) < 2:
                continue
            pid = parts[1].strip()
            if pid in known_pids:
                continue
            known_pids.add(pid)
            for p in (port_map.get(pid, ["?"])):
                rows.append((pid, "?", "SSM Plugin", p))
    except Exception:
        pass

    # Java processes: Kafka server + Kafdrop UI
    try:
        result = subprocess.run(["jps", "-l"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                pid, name = parts
                if pid in known_pids:
                    continue
                known_pids.add(pid)
                if "kafka.Kafka" in name:
                    rows.append((pid, "-", "Kafka Server", "9092"))
                elif "main.jar" in name:
                    rows.append((pid, "-", "Kafdrop UI", "8080"))
    except FileNotFoundError:
        pass
    except Exception:
        pass

    if not rows:
        info("No active Yappy-ToolKit processes found.")
        return

    table = Table(box=box.SIMPLE)
    table.add_column("PID", style="cyan")
    table.add_column("Env")
    table.add_column("Type")
    table.add_column("Port", style="yellow")

    for pid, env, kind, port in rows:
        table.add_row(pid, env, kind, port)

    console.print(table)


@app.command()
def edit():
    """Open the yappy project in VS Code."""
    project_root = get_project_root()
    info(f"Opening {project_root} in VS Code...")
    subprocess.run(["code", str(project_root)])


@app.command(name="py-purge")
def py_purge():
    """Clear pip cache."""
    info("Clearing pip cache...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "cache", "purge"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        success("Pip cache purged")
    else:
        die(f"Failed to purge pip cache: {result.stderr.strip()}")


def _replace_wrapper_with_eval(bashrc: Path):
    content = bashrc.read_text()
    lines = content.splitlines()
    cleaned = []
    skip = False
    for line in lines:
        if line.strip() == "yappy() {":
            skip = True
        if not skip:
            cleaned.append(line)
        if skip and line.strip() == "}":
            skip = False
    cleaned.append('')
    cleaned.append('eval "$(yappy init bash)"')
    bashrc.write_text("\n".join(cleaned) + "\n")


def _setup_config(config_dir: Path):
    examples = sorted(config_dir.glob("*.example"))
    for example in examples:
        target_name = example.name.replace(".example", "")
        # skip generic template pattern
        if target_name == "env.environment":
            continue
        target = config_dir / target_name
        if target.exists():
            success(f"  {target_name} already exists")
        else:
            warn(f"  {target_name} not found")
            yn = input(f"  Create from {example.name}? (Y/n): ")
            if yn.lower() != "n":
                content = example.read_text()
                target.write_text(content)
                success(f"  Created {target_name} — edit values before using")
                info(f"    -> yappy edit")


@app.command()
def setup():
    """One-time project setup: shell integration, config, dependencies."""
    project_root = get_project_root()
    migrated_resources = migrate_backend_resources(project_root)
    config_dir = project_config_dir(project_root)

    info("=== Yappy Setup ===")
    print()
    if migrated_resources:
        success(f"Backend resources moved: {', '.join(migrated_resources)}")
        print()

    # 1. Ensure Python Scripts directory is on PATH
    bashrc = Path.home() / ".bashrc"
    scripts_dir = Path(sys.executable).parent / "Scripts"
    scripts_posix = win_to_posix(str(scripts_dir))
    path_export = f'export PATH="$PATH:{scripts_posix}"'

    bashrc_content = bashrc.read_text() if bashrc.exists() else ""
    if scripts_posix in bashrc_content:
        success(f"Python Scripts PATH already in .bashrc")
    elif scripts_dir.exists():
        with open(bashrc, "a") as f:
            f.write(f"\n# yappy: Python Scripts on PATH\n{path_export}\n")
        success(f"Added Python Scripts to PATH in .bashrc")
        bashrc_content = bashrc.read_text()
    else:
        warn(f"Scripts dir not found: {scripts_dir}")

    # 2. Shell integration
    eval_marker = 'eval "$(yappy init bash)"'
    has_eval = eval_marker in bashrc_content
    has_wrapper = "yappy() {" in bashrc_content

    if has_eval:
        success("Shell integration already in .bashrc")
    elif has_wrapper:
        info("Found manual yappy() wrapper — replace with eval line?")
        yn = input("  Auto-replace? (Y/n): ")
        if yn.lower() != "n":
            _replace_wrapper_with_eval(bashrc)
            success("Replaced wrapper with eval integration")
        else:
            info("  Skipped")
    elif bashrc.exists():
        with open(bashrc, "a") as f:
            f.write(f"\n{eval_marker}\n")
        success(f"Added shell integration to {bashrc}")
    else:
        warn(f"No .bashrc found at {bashrc}")
        info(f"  Add manually:\n  {eval_marker}")

    # 3. Config files
    print()
    info("Config files:")
    _setup_config(config_dir)

    # 4. Dependencies
    print()
    info("Dependencies:")

    # 4.1 pip upgrade
    pip_upgrade = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True,
    )
    if pip_upgrade.returncode == 0:
        success("  pip upgraded")
    else:
        warn("  pip upgrade failed — continuing with the current pip")

    # 4.2 Backend: editable install resolves deps from docs/requirements.txt
    _install_backend_deps(project_root)

    # 4.3 Frontend: node_modules for the Angular app
    _install_frontend_deps(project_root / "frontend")

    # 4.4 External tools (not installed by pip)
    for cmd_name in ("aws", "session-manager-plugin"):
        result = subprocess.run(
            ["where", cmd_name] if sys.platform == "win32" else ["which", cmd_name],
            capture_output=True, shell=True,
        )
        if result.returncode == 0:
            success(f"  {cmd_name} found")
        else:
            warn(f"  {cmd_name} not found — install it first")

    # 5. Kafka (auto-download if missing)
    print()
    info("Kafka:")
    from yappy_library.adapters.kafka.setup import setup_kafka, setup_kafka_configs
    cfg = Config()
    setup_kafka_configs(cfg)
    kafka_ready = setup_kafka(cfg)
    if kafka_ready:
        success("  Kafka is ready")
    else:
        warn("  Kafka needs manual download — see messages above")

    # 6. AWS profile
    print()
    info("AWS profile:")
    result = subprocess.run(
        ["aws", "configure", "list", "--profile", cfg.profile],
        capture_output=True, text=True, shell=True,
    )
    if result.returncode == 0:
        success(f"  Profile '{cfg.profile}' configured")
    else:
        warn(f"  Profile '{cfg.profile}' not found — configure it with 'aws configure'")

    print()
    success("Setup complete. Run 'source ~/.bashrc' to load changes.")


@app.command()
def update():
    """Pull latest code and reinstall the editable package."""
    project_root = get_project_root()

    info("=== Yappy Update ===")
    print()

    # 1. Pull latest code
    info("1/2 Pulling latest code...")
    pull = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=str(project_root), text=True, check=False,
    )
    if pull.returncode != 0:
        warn("  git pull failed — continuing with current code")

    # 2. Reinstall editable (refresh entry points, deps, version metadata)
    print()
    info("2/2 Reinstalling package (pip install -e .)...")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(project_root)],
        capture_output=True, text=True, check=False,
    )
    if install.returncode != 0:
        die(f"  Reinstall failed: {install.stderr.strip()}")

    print()
    success("Update complete")
    version()


@app.command()
def uninstall(
    purge: bool = typer.Option(False, "--purge", help="Remove ALL dependencies (may break other packages)"),
):
    """Remove all yappy artifacts: Kafka, logs, tracker, shell integration."""
    import shutil

    project_root = get_project_root()
    yappy_home = Path.home() / ".yappy"

    info("=== Yappy Uninstall ===")
    print()

    # 1. Kill running Kafka processes
    info("Stopping Kafka processes...")
    try:
        result = subprocess.run(
            ["jps", "-l"], capture_output=True, text=True, check=False,
        )
        killed = 0
        for line in result.stdout.strip().splitlines():
            if "kafka.Kafka" in line or "main.jar" in line:
                pid = line.split()[0]
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                killed += 1
        if killed:
            success(f"  Killed {killed} process(es)")
        else:
            success("  No Kafka processes running")
    except FileNotFoundError:
        warn("  jps not found — skipping process check")

    # 2. Delete Kafka binaries (keep config/)
    print()
    info("Kafka files:")
    kafka_dir = project_root / "devkit" / "kafka"
    for name in ("kafka-core", "kafka-ui", "temp-logs"):
        d = kafka_dir / name
        if d.exists():
            shutil.rmtree(d)
            success(f"  Removed {d}")
    if not (kafka_dir / "kafka-core").exists() and not (kafka_dir / "kafka-ui").exists():
        success("  Binaries already clean")

    # 3. Delete ~/.yappy (tracker)
    print()
    info("Cache and logs:")
    if yappy_home.exists():
        shutil.rmtree(yappy_home)
        success(f"  Removed {yappy_home}")
    else:
        success("  Not found (already clean)")

    # 4. Delete Kafka storage in /tmp
    print()
    info("Kafka storage:")
    tmp_dirs = [
        Path("/tmp/kraft-combined-logs"),
        Path(os.environ.get("TEMP", "")) / "kraft-combined-logs",
    ]
    cleaned = False
    for d in tmp_dirs:
        if d.exists():
            try:
                shutil.rmtree(d)
                success(f"  Removed {d}")
                cleaned = True
            except PermissionError:
                warn(f"  {d} locked - reboot or delete manually")
                cleaned = True
    if not cleaned:
        success("  Not found (already clean)")

    # 5. Remove shell integration from .bashrc
    print()
    info("Shell integration:")
    bashrc = Path.home() / ".bashrc"
    if bashrc.exists():
        content = bashrc.read_text()
        marker = 'eval "$(yappy init bash)"'
        if marker in content:
            lines = content.splitlines()
            lines = [l for l in lines if marker not in l]
            bashrc.write_text("\n".join(lines) + "\n")
            success(f"  Removed eval line from {bashrc}")
        else:
            success("  No eval line found (already clean)")
    else:
        success("  No .bashrc found")

    print()
    success("Uninstall complete. Repo is clean.")

    # 6. Uninstall Python package + dependencies (last — code already in memory)
    print()
    info("Uninstalling Python package...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "yappy-cli-manager", "-y"],
        capture_output=True, text=True, cwd=str(project_root),
    )
    if result.returncode == 0:
        success("  Package removed")
    else:
        warn(f"  Could not uninstall: {result.stderr.strip()}")

    # Remove dependencies that are no longer needed (read from pyproject.toml)
    import tomllib
    pyproject = project_root / "pyproject.toml"
    deps = []
    if pyproject.exists():
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        deps = [
            d.split(">=")[0].split("~=")[0].split("==")[0].split("<")[0].strip()
            for d in data.get("project", {}).get("dependencies", [])
        ]

    if deps:
        removed = []
        kept = []
        for dep in deps:
            check = subprocess.run(
                [sys.executable, "-m", "pip", "show", dep],
                capture_output=True, text=True,
            )
            if check.returncode != 0:
                continue
            required_by = ""
            for line in check.stdout.splitlines():
                if line.startswith("Required-by:"):
                    required_by = line.split(":", 1)[1].strip()
            if purge or not required_by:
                subprocess.run(
                    [sys.executable, "-m", "pip", "uninstall", dep, "-y"],
                    capture_output=True,
                )
                removed.append(dep)
            else:
                kept.append(dep)
        if removed:
            success(f"  Dependencies removed: {', '.join(removed)}")
        if kept:
            info(f"  Dependencies kept (used by others): {', '.join(kept)}")


if __name__ == "__main__":
    app()
