from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

from yappy_library.adapters import process_tracker
from yappy_library.adapters.logging import info, raw, success, warn
from yappy_library.adapters.processes import (
    jvm_shim_command,
    open_url,
    own_console_popen_kwargs,
    stop_gracefully,
)
from yappy_library.config import Config

#: How long a broker gets to leave by itself before it is killed.
#:
#: Kafka's shutdown closes the metadata log, and that is the whole point: a log
#: closed is a log that is not corrupt. Thirty seconds is generous for a local
#: broker and short enough that a developer does not think the button is stuck.
GRACEFUL_STOP_SECONDS = 30.0


class KafkaUnavailable(Exception):
    """Kafka cannot be run from here, and the reason to show for it.

    This exists because :func:`yappy_library.adapters.logging.die` is the wrong
    thing for a library to do, and ``die()`` was what this module used to do.

    ``die()`` ends the *process*. That is right for ``yappy kafka up`` and fatal
    everywhere else, because the web server drives this very code:
    ``actions._start_kafka`` calls :meth:`KafkaService.up` from the sync endpoint
    ``POST /api/workflow/resources/start``, Starlette runs that in an anyio worker
    thread, anyio re-raises whatever came out into the event-loop task, and
    ``SystemExit`` is a ``BaseException`` — ``asyncio.Task.__step`` re-raises it
    explicitly and it escapes ``run_until_complete``. uvicorn is gone, with no
    traceback and nothing in the log. A missing ``libs/`` directory did not print
    an error on the screen; it took the whole UI down.

    The symptom only shows up on a machine with a broken Kafka install, which is
    why it survived: the happy path never reaches a ``die()``.

    **What a caller does with it is its own decision.** A terminal command turns it
    back into the exit it used to be (``cli/yappy_cli/kafka/manager.py``); the web
    turns it into a ``StartRefused`` so the row says why instead of the server
    vanishing. Nothing in this module calls ``die()`` anymore — that invariant is
    pinned by ``tests/test_kafka_unavailable.py``.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _kafka_bin(cfg: Config, script: str) -> Path:
    """Resolve a bin/*.sh script for the configured Kafka install.

    Uses bash scripts instead of .bat to avoid Windows path length issues.
    """
    return Path(cfg.kafka_core_path) / "bin" / script


def _ensure_windows():
    if sys.platform != "win32":
        raise KafkaUnavailable("Kafka management currently supports Windows only")


def _on_rm_error(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _stop_tracked_brokers() -> None:
    """Ask the brokers this project started to leave, one by one.

    The tracker knows them by the pid of their shim —the process that watches
    the JVM— and that shim's console *is* the broker's console, so the event
    reaches the JVM. The shim lives exactly as long as the JVM, so waiting for
    the shim to go is waiting for the broker to have finished closing.
    """
    for entry in process_tracker.get_tracked_processes(resource="kafka", target="server"):
        pid = entry.get("pid")
        if pid:
            _stop_java(str(pid))


def _kill_kafka_processes():
    """Stop the brokers: the ones we started, politely; the rest, as before.

    The sweep by name is the hard kill it has always been, and it is left that
    way on purpose: a broker somebody started by hand lives in *their* console,
    and a ``CTRL_C`` there would land in the middle of whatever they were doing.
    """
    _stop_tracked_brokers()
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


def _stop_java(pid: str) -> bool:
    """Ask a JVM to leave, and kill it only if it doesn't.

    **A broker that is killed hard leaves its metadata log half-written.** The
    next start finds the invalid offset, deletes the segment and dies with an
    NPE — and from there on every start fails the same way, until somebody wipes
    the storage. Asking first is what keeps the storage usable.

    The graceful way needs a console to send the event to, which is why the
    broker is spawned with one (see
    :func:`~yappy_library.adapters.processes.own_console_popen_kwargs`).
    """
    try:
        pid_number = int(pid)
    except (TypeError, ValueError):
        return False
    if stop_gracefully(pid_number, timeout=GRACEFUL_STOP_SECONDS):
        raw(f"  - el proceso {pid} se cerró solo")
        return True
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    return False


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
                    _stop_java(pid)
                    killed = True
        if not killed:
            warn("No se encontró proceso de Kafka UI")
        else:
            time.sleep(1)
            success("Kafdrop UI stopped")
    except FileNotFoundError:
        warn("jps no encontrado, no se pudieron buscar procesos Java")


#: Port the bundled Kafdrop listens on. It is passed on the command line a few
#: lines below, so tracking it needs the same value rather than a second guess.
KAFDROP_UI_PORT = 8080

#: Only used when `server.properties` is missing or unreadable. It is not a
#: default to configure the broker with — see `_broker_port`.
DEFAULT_BROKER_PORT = 9092


def _port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Whether something is already listening on ``port``.

    A connection attempt and not a scan: the question is not "who holds the
    port" but "is somebody there", and the answer only has to be good enough to
    not start a second broker.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _broker_port(props: Path) -> int:
    """The port the broker accepts clients on, read from its own configuration.

    ``listeners`` can name more than one listener, and the one a KRaft controller
    runs is *not* it — ``PLAINTEXT://:9092,CONTROLLER://:9093`` serves the
    controller on 9093. Taking the last ``:`` of the line, which is what this did
    first, returns the controller port and makes every readiness probe talk to a
    port where nothing speaks Kafka.

    Hardcoding a default instead would put a second source of truth next to the
    one ``setup_kafka_configs`` writes. A missing or unreadable file falls back to
    9092 so a broken config is not fatal: the caller is recording a hint for a UI,
    not configuring the broker.
    """
    try:
        for line in props.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() != "listeners":
                continue
            for listener in value.strip().split(","):
                name, sep, address = listener.partition("://")
                if not sep or name.strip().upper() == "CONTROLLER":
                    continue
                return int(address.strip().rsplit(":", 1)[-1])
    except (OSError, ValueError):
        pass
    return DEFAULT_BROKER_PORT


def _open_kafdrop_browser() -> None:
    """Open Kafdrop in the default browser.

    Only the caller decides *whether* to call this — see ``_start_ui``. Both
    platform branches lived inline in two places and neither of them knew who
    was asking: a background start driven by the web server opened a browser on
    the developer's machine even though the screen already renders the "Abrir ↗"
    link for that exact row (``registry.py`` sets ``browser_url`` for it). From a
    terminal the opposite is true: opening the link beats making someone go find
    a button, and spawning another terminal just to click one is worse.

    The *how* is `open_url`'s now — this used to be a raw ``os.system``, which
    puts the child in the caller's console group.
    """
    open_url(f"http://localhost:{KAFDROP_UI_PORT}")


class KafkaService:
    def __init__(self, cfg: Config | None = None):
        self._cfg = cfg or Config()
        self._kafka_core = Path(self._cfg.kafka_core_path)
        self._kafka_ui = Path(self._cfg.kafka_ui_path)
        self._procs: list[subprocess.Popen] = []
        self._log_dir = Path(self._cfg.kafka_path) / "temp-logs"

    def _spawn_detached(
        self, cmd: list[str], target: str, local_port: int | None = None,
        owner: str = "cli",
    ) -> subprocess.Popen:
        """Spawn a background process with output captured to a real log file.

        The log file lives at ~/.yappy/logs/kafka-<target>-<pid>.log. The pid is
        only known after Popen returns, so the process writes to a placeholder
        first and a hard link with the pid in its name is created right after.

        **"Detached" es literal.** Sin `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
        el hijo queda en el mismo grupo de consola que el server, y en Windows eso
        significa que un Ctrl+C del grupo lo alcanza a los dos. El broker se
        llevaba por delante al proceso que lo levantó — que es exactamente lo que
        pasaba al arrancar Kafka desde la pantalla. El refresher de DB
        (`db/tunnel.py::_start_detached_refresher`) ya lo hacía así; esta función
        tenía el nombre pero no el comportamiento. Los flags viven hoy en un solo
        lugar, `processes.detached_popen_kwargs`, que también los usa `open_url`.

        **El broker, en cambio, tiene consola propia.** Es lo que le permite
        irse solo cuando se le pide: un ``CTRL_C`` es lo que corre los shutdown
        hooks del JVM, los que cierran el metadata log, y un ``CTRL_C`` sólo
        llega a un proceso que es el único de su consola. Un broker al que se lo
        mata a la fuerza deja el storage a medio escribir, y del arranque
        siguiente en adelante todos fallan igual. El costo está anotado en
        `processes.own_console_popen_kwargs`: la consola es invisible y el
        broker vive en su propio grupo, así que no le llega el Ctrl+C.
        """
        self._log_dir.mkdir(parents=True, exist_ok=True)
        placeholder = self._log_dir / f"kafka-{target}.log"
        handle = placeholder.open("ab")
        kwargs = {
            "stdout": handle,
            "stderr": subprocess.STDOUT,
            "cwd": str(self._log_dir),
            **own_console_popen_kwargs(),
        }
        proc = subprocess.Popen(jvm_shim_command(cmd), **kwargs)
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
            pid=proc.pid,
            resource="kafka",
            target=target,
            log_file=str(log_path),
            local_port=local_port,
            owner=owner,
        )
        self._procs.append(proc)
        return proc

    def _tracked_broker_alive(self) -> bool:
        """Whether this project already started a broker that is still running.

        The tracker knows about the brokers *we* started, including one that has
        not bound its port yet —which is exactly the window in which a second
        click used to slip a second JVM onto the same storage.
        """
        for entry in process_tracker.get_tracked_processes(resource="kafka", target="server"):
            if entry.get("alive"):
                return True
        return False

    def _start_server(self, detach: bool, owner: str = "cli") -> subprocess.Popen | None:
        props = self._kafka_core / "config" / "kraft" / "server.properties"
        libs_dir = self._kafka_core / "libs"
        if not props.exists():
            raise KafkaUnavailable(f"Kafka properties not found: {props}")
        if not libs_dir.exists():
            raise KafkaUnavailable(f"Kafka libs not found: {libs_dir}")

        port = _broker_port(props)

        # **Two brokers on one storage are what corrupt it.** The second JVM
        # opens the same ``log.dirs`` and its recovery deletes what the first
        # one is writing —"Found invalid offset during recovery. Deleting the
        # corrupt segment"— and then dies with an NPE because the index it just
        # created is empty. The storage is left broken and *every* start from
        # then on fails the same way, until somebody wipes it.
        #
        # So the check is in the service and not in the screen: the web and the
        # CLI both come through here, and the CLI is the one that has no button
        # to hide behind.
        if self._tracked_broker_alive():
            raise KafkaUnavailable(
                "Ya hay un broker de Kafka corriendo. No se puede levantar un "
                "segundo: los dos escribirían el mismo storage y lo dejarían "
                "corrupto. Paralo primero."
            )
        if _port_in_use(port):
            raise KafkaUnavailable(
                f"El puerto {port} ya está en uso. Si ese proceso es un broker de "
                "Kafka, no se puede levantar un segundo: los dos escribirían el "
                "mismo storage y lo dejarían corrupto."
            )

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
            proc = self._spawn_detached(cmd, "server", port, owner)
            success("Kafka server started (background)")
            time.sleep(2)
            return proc

        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            success("Kafka server stopped (Ctrl+C)")
        return None

    def _start_ui(self, detach: bool, owner: str = "cli") -> subprocess.Popen | None:
        ui_jar = self._kafka_ui / "main.jar"
        if not ui_jar.exists():
            raise KafkaUnavailable(f"Kafdrop UI jar not found: {ui_jar}")
        info(f"Starting Kafdrop UI on port {KAFDROP_UI_PORT}...")

        cmd = [
            "java", "-jar", str(ui_jar),
            f"--server.port={KAFDROP_UI_PORT}",
        ]
        # Config is in devkit/kafka/config/ui/config.yml
        ui_config = Path(self._cfg.kafka_path) / "config" / "ui" / "config.yml"
        if ui_config.exists():
            cmd.append(f"--spring.config.additional-location=file:{ui_config}")

        if detach:
            proc = self._spawn_detached(cmd, "ui", KAFDROP_UI_PORT, owner)
            success("Kafdrop UI started (background)")
            time.sleep(2)
            # Only from a terminal. The web path gets the "Abrir ↗" link in the
            # row it just drew, and stealing the developer's focus with a second
            # browser window they did not ask for is the cost of the duplicate.
            if owner == process_tracker.OWNER_CLI:
                _open_kafdrop_browser()
            return proc

        # Foreground: the terminal is blocked on this process, so open first.
        _open_kafdrop_browser()
        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            success("Kafdrop UI stopped (Ctrl+C)")
        return None

    def up(
        self, target: str, detach: bool = False, owner: str = "cli"
    ) -> subprocess.Popen | None:
        _ensure_windows()
        if target == "server":
            return self._start_server(detach, owner)
        if target == "ui":
            return self._start_ui(detach, owner)
        if target == "clean":
            self.clean()
            return None
        raise KafkaUnavailable("Invalid action. Use: server, ui, or clean")

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
            raise KafkaUnavailable("Error generando UUID")
        raw(f"  UUID: {uuid}")

        raw("Formateando el storage...")
        output = self._run_kafka_tool(
            "kafka.tools.StorageTool",
            ["format", "-t", uuid, "-c", str(props), "--standalone"],
        )
        if output is not None and ("Formatting" in output or output == ""):
            raw("Storage reset completado")
        else:
            raise KafkaUnavailable("Error formateando storage")

        # `_kill_kafka_processes` killed them by name, which knows nothing about the
        # tracker: without this, every entry the tracker still holds for a broker
        # that is now dead becomes a ghost that `reconcile()` has to clean up on the
        # next `yappy web`, and the row reads `dead` instead of `down` until then.
        # `down()` has always done this; `clean()` did not, and from the web it
        # would have shown up immediately.
        self._untrack("server")
        self._untrack("ui")

    def _untrack(self, target: str) -> None:
        for p in process_tracker.get_tracked_processes(resource="kafka", target=target):
            pid = p.get("pid")
            if pid:
                process_tracker.untrack_process(pid)

    def down(self, target: str) -> None:
        _ensure_windows()
        if target == "server":
            info("Waiting for Kafka to stop...")
            # The broker is asked to leave first: its shutdown closes the
            # metadata log, and a log closed is a log that is not corrupt. The
            # hard kill stays as the fallback for a broker that does not take
            # the hint.
            _stop_tracked_brokers()
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
            raise KafkaUnavailable("Invalid target. Use: server or ui")
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
