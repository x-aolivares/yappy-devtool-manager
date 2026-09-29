"""Kafka setup: download server + UI if not installed."""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from ..config import Config
from ..logger import info, success, warn

# Versions — edit here to upgrade
KAFKA_VERSION = "4.3.1"
KAFKA_SCALA = "2.13"
KAFDROP_VERSION = "4.2.0"

# URLs
KAFKA_URL = (
    f"https://downloads.apache.org/kafka/{KAFKA_VERSION}"
    f"/kafka_{KAFKA_SCALA}-{KAFKA_VERSION}.tgz"
)
KAFDROP_URL = (
    f"https://github.com/obsidiandynamics/kafdrop/releases/download/"
    f"{KAFDROP_VERSION}/kafdrop-{KAFDROP_VERSION}.jar"
)


# ── Helpers ───────────────────────────────────────────────────────

def _download(url: str, dest: Path) -> bool:
    """Download file. Returns True if ok."""
    try:
        info(f"  Downloading {dest.name}...")
        urllib.request.urlretrieve(url, str(dest))
        return True
    except Exception as e:
        warn(f"  Download failed: {e}")
        return False


def _extract(tar_path: Path, dest: Path) -> bool:
    """Extract .tgz. Returns True if ok."""
    try:
        info("  Extracting...")
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path=dest)
        return True
    except Exception as e:
        warn(f"  Extraction failed: {e}")
        return False


def _format_kafka_storage(core: Path) -> bool:
    """Format Kafka storage for KRaft. Returns True if ok."""
    import shutil as _shutil
    import stat as _stat
    storage_sh = core / "bin" / "kafka-storage.sh"
    props = core / "config" / "kraft" / "server.properties"

    if not storage_sh.exists() or not props.exists():
        return False

    # Clean old storage before formatting (with Windows permission fix)
    def _on_rm_error(func, path, exc_info):
        os.chmod(path, _stat.S_IWRITE)
        func(path)

    for line in props.read_text().splitlines():
        line = line.strip()
        if line.startswith("log.dirs="):
            for d in line.split("=", 1)[1].split(","):
                p = Path(d.strip())
                if not p.is_absolute():
                    p = core / p
                if p.exists():
                    try:
                        _shutil.rmtree(p, onerror=_on_rm_error)
                    except Exception:
                        pass  # will fail on format step if still locked

    # Find bash (Git Bash on Windows)
    bash = _find_bash()
    if not bash:
        warn("  bash not found — storage not formatted")
        return False

    # Set correct log4j for bash
    env = os.environ.copy()
    log4j = f"file:///{core}/config/tools-log4j2.yaml".replace("\\", "/")
    env["KAFKA_LOG4J_OPTS"] = f"-Dlog4j2.configurationFile={log4j}"

    info("  Formatting Kafka storage (KRaft)...")
    result = subprocess.run(
        [bash, str(storage_sh), "random-uuid"],
        capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode != 0:
        warn(f"  Could not generate UUID: {result.stderr.strip()}")
        return False

    uuid = result.stdout.strip()
    result = subprocess.run(
        [bash, str(storage_sh), "format", "-t", uuid, "-c", str(props),
         "--standalone"],
        capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode == 0:
        success("  Storage formatted")
        return True
    else:
        warn(f"  Storage format failed: {result.stderr.strip()}")
        return False


def _find_bash() -> str | None:
    """Find bash executable on Windows."""
    import shutil
    bash = shutil.which("bash")
    if bash:
        return bash
    # Try common Git Bash locations
    for path in [
        "C:\\Program Files\\Git\\bin\\bash.exe",
        "C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ]:
        if Path(path).exists():
            return path
    return None


# ── Main ──────────────────────────────────────────────────────────

def setup_kafka(cfg: Config | None = None) -> bool:
    """Download Kafka + Kafdrop if missing. Returns True when ready."""
    cfg = cfg or Config()
    base = Path(cfg.kafka_path)
    core = Path(cfg.kafka_core_path)
    ui = Path(cfg.kafka_ui_path)

    base.mkdir(parents=True, exist_ok=True)
    ready = True

    # Server
    if (core / "bin" / "kafka-server-start.sh").exists():
        success(f"  Kafka server OK ({KAFKA_VERSION})")
    else:
        info("  Kafka server not found — installing...")
        tar = base / f"kafka_{KAFKA_SCALA}-{KAFKA_VERSION}.tgz"
        extracted = base / f"kafka_{KAFKA_SCALA}-{KAFKA_VERSION}"

        if _download(KAFKA_URL, tar) and _extract(tar, base):
            if extracted.exists():
                if core.exists():
                    shutil.rmtree(core)
                extracted.rename(core)
            tar.unlink(missing_ok=True)

            # Create kraft config (expected by KafkaService)
            kraft_dir = core / "config" / "kraft"
            kraft_dir.mkdir(parents=True, exist_ok=True)
            src_props = core / "config" / "server.properties"
            dst_props = kraft_dir / "server.properties"
            if src_props.exists() and not dst_props.exists():
                shutil.copy2(src_props, dst_props)

            # Format storage for fresh install
            _format_kafka_storage(core)

            success(f"  Kafka server installed")
        else:
            warn(f"  Manual download: {KAFKA_URL}")
            ready = False

    # UI
    if (ui / "main.jar").exists():
        success(f"  Kafdrop UI OK ({KAFDROP_VERSION})")
    else:
        info("  Kafdrop UI not found — installing...")
        ui.mkdir(parents=True, exist_ok=True)
        jar = ui / "main.jar"

        if _download(KAFDROP_URL, jar):
            success(f"  Kafdrop UI installed")
        else:
            warn(f"  Manual download: {KAFDROP_URL}")
            ready = False

    return ready


def setup_kafka_configs(cfg: Config | None = None) -> None:
    """Create placeholder config files if missing."""
    cfg = cfg or Config()
    base = Path(cfg.kafka_path)

    files = {
        base / "config" / "server" / "server.properties": (
            "# Kafka Server (KRaft Mode)\n"
            "# See: https://kafka.apache.org/documentation/#configuration\n"
        ),
        base / "config" / "ui" / "config.yml": (
            "# Kafdrop UI — works out of the box\n"
            "# See: https://github.com/obsidiandynamics/kafdrop#configuration\n"
        ),
    }

    for path, content in files.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            info(f"  Created {path.name}")
