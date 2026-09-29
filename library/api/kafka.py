from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from .. import process_tracker
from ..config import Config
from ..logger import info, success, warn, die, raw


def _kafka_bin(cfg: Config, script: str) -> Path:
    """Resolve a bin/*.sh script for the configured Kafka install.

    Uses bash scripts instead of .bat to avoid Windows path length issues.
    """
    return Path(cfg.kafka_core_path) / "bin" / script


def _ensure_windows():
    if sys.platform != "win32":
        die("Kafka management currently supports Windows only")


def _on_rm_error(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _kill_kafka_processes():
    try:
        result = subprocess.run(["jps", "-l"], capture_output=True, text=True, check=False)
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                pid, name = parts
                if "kafka.Kafka" in name or "main.jar" in name:
                    raw(f"  - matando proceso {name} (PID {pid})")
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        time.sleep(1)
    except FileNotFoundError:
        warn("jps no encontrado, no se pudieron buscar procesos Java")


def _kill_ui_process():
    try:
        result = subprocess.run(["jps", "-l"], capture_output=True, text=True, check=False)
        killed = False
        for line in result.stdout.strip().splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                pid, name = parts
                if "main.jar" in name:
                    raw(f"  - matando UI (PID {pid})")
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                    killed = True
        if not killed:
            warn("No se encontró proceso de Kafka UI")
        else:
            time.sleep(1)
            success("Kafdrop UI stopped")
    except FileNotFoundError:
        warn("jps no encontrado, no se pudieron buscar procesos Java")


class KafkaService:
    def __init__(self, cfg: Config | None = None):
        self._cfg = cfg or Config()
        self._kafka_core = Path(self._cfg.kafka_core_path)
        self._kafka_ui = Path(self._cfg.kafka_ui_path)
        self._procs: list[subprocess.Popen] = []
        self._log_dir = Path(self._cfg.kafka_path) / "temp-logs"

    def _spawn_detached(self, cmd: list[str], target: str) -> subprocess.Popen:
        """Spawn a background process with output captured to a real log file.

        The log file lives at ~/.yappy/logs/kafka-<target>-<pid>.log. The pid is
        only known after Popen returns, so the process writes to a placeholder
        first and a hard link with the pid in its name is created right after.
        """
        self._log_dir.mkdir(parents=True, exist_ok=True)
        placeholder = self._log_dir / f"kafka-{target}.log"
        handle = placeholder.open("ab")
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, cwd=str(self._log_dir))
        handle.close()

        log_path = self._log_dir / f"kafka-{target}-{proc.pid}.log"
        try:
            os.link(placeholder, log_path)
            try:
                placeholder.unlink()
            except OSError:
                pass
        except OSError:
            log_path = placeholder

        process_tracker.track_process(
            pid=proc.pid, resource="kafka", target=target, log_file=str(log_path)
        )
        self._procs.append(proc)
        return proc

    def _start_server(self, detach: bool) -> subprocess.Popen | None:
        props = self._kafka_core / "config" / "kraft" / "server.properties"
        libs_dir = self._kafka_core / "libs"
        if not props.exists():
            die(f"Kafka properties not found: {props}")
        if not libs_dir.exists():
            die(f"Kafka libs not found: {libs_dir}")
        info("Starting Kafka server (KRaft)...")

        # Build classpath from all JARs in libs/
        jars = list(libs_dir.glob("*.jar"))
        classpath = ";".join(str(j) for j in jars)

        # Log4j config
        log4j = (self._kafka_core / "config" / "log4j2.yaml").resolve()
        temp_logs = str(Path(self._cfg.kafka_path) / "temp-logs")

        cmd = [
            "java",
            "-Xmx1G", "-Xms1G",
            f"-Dkafka.logs.dir={temp_logs}",
            f"-Dlog4j2.configurationFile=file:///{log4j}",
            "-cp", classpath,
            "kafka.Kafka",
            str(props),
        ]

        if detach:
            proc = self._spawn_detached(cmd, "server")
            success("Kafka server started (background)")
            time.sleep(2)
            return proc

        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            success("Kafka server stopped (Ctrl+C)")
        return None

    def _start_ui(self, detach: bool) -> subprocess.Popen | None:
        ui_jar = self._kafka_ui / "main.jar"
        if not ui_jar.exists():
            die(f"Kafdrop UI jar not found: {ui_jar}")
        info("Starting Kafdrop UI on port 8080...")

        cmd = [
            "java", "-jar", str(ui_jar),
            "--server.port=8080",
        ]
        # Config is in devkit/kafka/config/ui/config.yml
        ui_config = Path(self._cfg.kafka_path) / "config" / "ui" / "config.yml"
        if ui_config.exists():
            cmd.append(f"--spring.config.additional-location=file:{ui_config}")

        if detach:
            proc = self._spawn_detached(cmd, "ui")
            success("Kafdrop UI started (background)")
            time.sleep(2)
            # Open browser
            if sys.platform == "win32":
                os.system('start "" "http://localhost:8080"')
            else:
                import webbrowser
                webbrowser.open("http://localhost:8080")
            return proc

        # Open browser before blocking
        if sys.platform == "win32":
            os.system('start "" "http://localhost:8080"')
        else:
            import webbrowser
            webbrowser.open("http://localhost:8080")
        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            success("Kafdrop UI stopped (Ctrl+C)")
        return None

    def up(self, target: str, detach: bool = False) -> subprocess.Popen | None:
        _ensure_windows()
        if target == "server":
            return self._start_server(detach)
        if target == "ui":
            return self._start_ui(detach)
        if target == "clean":
            self.clean()
            return None
        die("Invalid action. Use: server, ui, or clean")

    def _run_kafka_tool(self, classname: str, args: list[str]) -> str | None:
        """Run a Kafka tool class directly via Java. Returns stdout or None."""
        libs_dir = self._kafka_core / "libs"
        jars = list(libs_dir.glob("*.jar"))
        classpath = ";".join(str(j) for j in jars)

        log4j = (self._kafka_core / "config" / "tools-log4j2.yaml").resolve()

        cmd = [
            "java",
            "-Xmx256M",
            f"-Dlog4j2.configurationFile=file:///{log4j}",
            "-cp", classpath,
            classname,
        ] + args

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            warn(f"  Tool failed: {result.stderr.strip()}")
            return None
        return result.stdout.strip()

    def _get_log_dirs(self) -> list[Path]:
        """Parse log.dirs from server.properties."""
        props = self._kafka_core / "config" / "kraft" / "server.properties"
        if not props.exists():
            return []
        dirs = []
        for line in props.read_text().splitlines():
            line = line.strip()
            if line.startswith("log.dirs="):
                value = line.split("=", 1)[1]
                for d in value.split(","):
                    p = Path(d.strip())
                    if not p.is_absolute():
                        p = self._kafka_core / p
                    dirs.append(p)
        return dirs

    def clean(self) -> None:
        _ensure_windows()
        kafka_logs = self._kafka_core / "logs"

        raw("Reiniciando el almacenamiento de Kafka...")
        raw("")
        raw("Limpiando archivos temporales y logs de Kafka...")

        _kill_kafka_processes()

        def _rm(path: Path):
            if not path.exists():
                return
            try:
                shutil.rmtree(path, onerror=_on_rm_error)
                raw(f"  - logs eliminados: {path}")
            except PermissionError as e:
                raw(f"  - no se pudo eliminar {path}: {e}")

        # Clean log.dirs from server.properties (e.g. /tmp/kraft-combined-logs)
        for log_dir in self._get_log_dirs():
            _rm(log_dir)
        _rm(kafka_logs)

        raw("")
        props = self._kafka_core / "config" / "kraft" / "server.properties"

        raw("Generando nuevo UUID para el storage...")
        uuid = self._run_kafka_tool("kafka.tools.StorageTool", ["random-uuid"])
        if uuid is None:
            die("Error generando UUID")
        raw(f"  UUID: {uuid}")

        raw("Formateando el storage...")
        output = self._run_kafka_tool(
            "kafka.tools.StorageTool",
            ["format", "-t", uuid, "-c", str(props), "--standalone"],
        )
        if output is not None and ("Formatting" in output or output == ""):
            raw("Storage reset completado")
        else:
            die("Error formateando storage")

    def _untrack(self, target: str) -> None:
        for p in process_tracker.get_tracked_processes(resource="kafka", target=target):
            pid = p.get("pid")
            if pid:
                process_tracker.untrack_process(pid)

    def down(self, target: str) -> None:
        _ensure_windows()
        if target == "server":
            info("Waiting for Kafka to stop...")
            # Kill Kafka Java process directly
            try:
                result = subprocess.run(["jps", "-l"], capture_output=True, text=True, check=False)
                for line in result.stdout.strip().splitlines():
                    if "kafka.Kafka" in line:
                        pid = line.split()[0]
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            except FileNotFoundError:
                warn("jps not found")
            success("Kafka server stopped")
        elif target == "ui":
            info("Stopping Kafdrop UI...")
            _kill_ui_process()
        else:
            die("Invalid target. Use: server or ui")
        self._untrack(target)

    def cleanup(self):
        for proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
        self._procs.clear()
        for target in ("server", "ui"):
            self._untrack(target)


class DevUtils:
    def __init__(self):
        self._cfg = Config()

    def kafka(self) -> KafkaService:
        return KafkaService(self._cfg)
