"""The Kafka service must not end the process it is running inside.

`die()` is `error()` plus `sys.exit()`, and `SystemExit` is a `BaseException`.
That is exactly the wrong property for a library the web server drives: `POST
/api/workflow/resources/start` is a sync `def`, so Starlette runs it in an anyio
worker thread, anyio re-raises whatever came out into the event-loop task,
`asyncio.Task.__step` re-raises `SystemExit` explicitly, and it escapes
`run_until_complete`. uvicorn is gone, with no traceback and nothing in the log —
the same silent death as the `taskkill /T` cascade.

The trigger is mundane: a Kafka install missing `libs/` or `main.jar`. It never
fires on a healthy machine, which is why it survived — and why on a broken one
the developer's whole UI disappears instead of the row saying why.

What is pinned here:

- the service raises `KafkaUnavailable` and never calls `die()` — asserted on the
  source, so a new `die()` anywhere in the file fails the build;
- every failure shape the web can reach raises it, not `SystemExit`;
- the web turns it into a `StartRefused`, which the route already answers with a
  409 carrying the reason;
- the terminal still exits the way it always did, with the same code and the same
  message on stderr. `die()` is correct for a command; it was only ever wrong for
  the library underneath it.
"""

from __future__ import annotations

import ast
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich import console as rich_console

import yappy_cli.kafka.manager as manager
import yappy_cli.workflow.executor as executor_cli
import yappy_library.adapters.kafka.service as api_kafka
from yappy_library.adapters import process_tracker
from yappy_library.adapters.kafka.service import KafkaService, KafkaUnavailable
from yappy_library.adapters.logging import _theme as _THEME
from yappy_library.application.workflow import actions
from yappy_library.application.workflow.actions import StartRefused


# --- fixtures -----------------------------------------------------------------


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """An empty tracker, so `ensure_port_free` sees the broker port as free."""
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


def _service(tmp_path, *, props=True, libs=True, jar=True) -> KafkaService:
    """A `KafkaService` over an install broken in exactly the way asked for.

    Each flag is one piece of the install: `server.properties`, the `libs/`
    directory, and Kafdrop's `main.jar`. Missing any one of them used to be a
    `die()`.
    """
    core = tmp_path / "kafka-core"
    ui = tmp_path / "kafka-ui"
    ui.mkdir(parents=True, exist_ok=True)

    if props:
        (core / "config" / "kraft").mkdir(parents=True, exist_ok=True)
        (core / "config" / "kraft" / "server.properties").write_text(
            "listeners=PLAINTEXT://:9092,CONTROLLER://:9093\n", encoding="utf-8"
        )
    if libs:
        (core / "libs").mkdir(parents=True, exist_ok=True)
    if jar:
        (ui / "main.jar").write_bytes(b"")

    cfg = SimpleNamespace(
        kafka_core_path=str(core),
        kafka_ui_path=str(ui),
        kafka_path=str(tmp_path / "kafka"),
    )
    return KafkaService(cfg)


def _fake_cfg(tmp_path) -> SimpleNamespace:
    """The slice of `Config` the actions and the registry read."""
    return SimpleNamespace(
        env="dev",
        db_port=8100,
        kafka_core_path=str(tmp_path / "no-existe"),
        kafka_ui_path=str(tmp_path / "no-existe-ui"),
        kafka_path=str(tmp_path / "no-existe-kafka"),
        get=lambda key, default=None: default,
        require=lambda key: "i-123",
        profile="base-profile",
    )


def _boom(reason):
    """A stand-in `up()` that fails the way a broken install fails."""
    def up(*args, **kwargs):
        raise KafkaUnavailable(reason)

    return up


# --- the invariant ------------------------------------------------------------


def test_the_kafka_service_never_calls_die():
    """The whole fix, asserted on the source rather than on a call.

    A test that exercises four failure shapes only proves those four. Someone
    adding a fifth `die()` — for a new check, in a helper, in `clean()` — would
    pass every behavioural test in this file. This one cannot: it parses the
    module and fails if `die` is imported or called at all.

    ``sys.exit()``, ``os._exit()`` and ``raise SystemExit`` are in the same net
    on purpose. ``die()`` was never the only door: it is just the one that was
    used, and the guard is worthless if the next person writes ``sys.exit(1)``
    instead and the suite stays green. They are all ``BaseException``, which is
    the entire problem.
    """
    tree = ast.parse(Path(api_kafka.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    called: set[str] = set()
    escapes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("logging"):
            imported.update(alias.asname or alias.name for alias in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            # `sys.exit` and `os._exit` — the two escapes that are not `die()`.
            # Matched on the attribute name, not the module: `os.chmod` and
            # `os.link` are ordinary file calls, not process endings.
            if node.func.attr in ("exit", "_exit"):
                escapes.append(f"{ast.unparse(node.func)}() at line {node.lineno}")
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            callee = node.exc.func
            if isinstance(callee, ast.Name) and callee.id == "SystemExit":
                escapes.append(f"raise SystemExit at line {node.lineno}")

    assert "die" not in imported, "the Kafka service must not import die()"
    assert "die" not in called, "the Kafka service must not call die()"
    assert not hasattr(api_kafka, "die")
    assert not escapes, (
        f"the Kafka service can end the process it runs inside: {escapes}. "
        f"SystemExit is a BaseException, so a sync endpoint that raises it takes "
        f"uvicorn down with no traceback."
    )


# --- what the web can reach ----------------------------------------------------


def test_a_missing_properties_file_raises_instead_of_exiting(tmp_path, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path, props=False).up("server", detach=True)

    assert "Kafka properties not found" in excinfo.value.reason


def test_a_missing_libs_directory_raises_instead_of_exiting(tmp_path, monkeypatch):
    """The half-extracted install: the config is there, the jars are not."""
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path, libs=False).up("server", detach=True)

    assert "Kafka libs not found" in excinfo.value.reason


def test_a_missing_kafdrop_jar_raises_instead_of_exiting(tmp_path, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path, jar=False).up("ui", detach=True)

    assert "Kafdrop UI jar not found" in excinfo.value.reason


def test_a_non_windows_machine_raises_instead_of_exiting(tmp_path, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "linux")

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path).up("server", detach=True)

    assert "Windows only" in excinfo.value.reason


def test_an_unknown_action_raises_instead_of_exiting(tmp_path, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path).up("serverr")

    assert "Invalid action" in excinfo.value.reason


@pytest.mark.parametrize("target", ["server", "ui"])
def test_nothing_on_the_webs_path_raises_a_base_exception(target, tmp_path, monkeypatch):
    """`SystemExit` is a `BaseException`: no `except Exception` catches it.

    The web's only defence used to be luck. Stating it here means a future change
    that swaps `KafkaUnavailable` back for `SystemExit` fails here with a reason
    that names the mechanism, instead of failing a route test with "connection
    reset".
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    broken = _service(tmp_path, props=False, libs=False, jar=False)

    try:
        broken.up(target, detach=True, owner=process_tracker.OWNER_WEB)
    except Exception as exc:  # noqa: BLE001 — what lands here is the point
        assert isinstance(exc, Exception)
        assert not isinstance(exc, SystemExit)
    except BaseException as exc:  # noqa: BLE001 — this arm is the failure
        pytest.fail(f"a BaseException escaped to uvicorn: {type(exc).__name__}: {exc}")
    else:
        pytest.fail("a Kafka install with no config, no libs and no jar started something")


# --- the refusal the screen shows ---------------------------------------------


def test_a_broken_install_is_a_refusal_not_a_dead_server(tmp_path, tracker, monkeypatch):
    """`actions.start_resource` turns the failure into something the row can show.

    `StartRefused` already has a shape for this: the route answers 409 with the
    reason in `detail`, and the screen renders it on the row. That is the
    difference between "Kafka Server: Kafka properties not found: …" and a UI
    that is simply not there any more.
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    cfg = _fake_cfg(tmp_path)
    monkeypatch.setattr(actions, "Config", lambda: cfg)

    with pytest.raises(StartRefused) as excinfo:
        actions.start_resource(cfg, "kafka-server")

    reason = excinfo.value.reason
    assert reason.startswith("No se pudo levantar Kafka Server")
    assert "Kafka properties not found" in reason


def test_the_refusal_names_the_ui_when_it_is_the_one_that_failed(tmp_path, tracker, monkeypatch):
    """Two rows, two broken halves, two different names on the message."""
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    cfg = _fake_cfg(tmp_path)
    monkeypatch.setattr(actions, "Config", lambda: cfg)
    monkeypatch.setattr(api_kafka.KafkaService, "up", _boom("Kafdrop UI jar not found: C:\\ui"))

    with pytest.raises(StartRefused) as excinfo:
        actions.start_resource(cfg, "kafka-ui")

    assert excinfo.value.reason.startswith("No se pudo levantar Kafdrop UI")
    assert "Kafdrop UI jar not found" in excinfo.value.reason


def test_the_route_answers_409_with_the_reason(tracker, monkeypatch):
    """End to end through the app, so the FastAPI layer is really in the path."""
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(
        api_kafka.KafkaService, "up",
        _boom("Kafka libs not found: C:\\kafka\\kafka-core\\libs"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workflow/resources/start?env=dev", json={"key": "kafka-server"}
        )

    assert response.status_code == 409
    assert "Kafka libs not found" in response.json()["detail"]


# --- the terminal is unchanged -------------------------------------------------


@pytest.fixture
def die_calls(monkeypatch):
    """Record every ``die()`` the command makes, and exit like the real one.

    Neither ``capsys`` nor ``capfd`` sees the message: ``logging.py`` builds its
    ``rich`` console with ``file=sys.stderr`` at *import* time, so the console
    holds the original object and keeps writing to it no matter what either
    capture fixture installs. Swapping the console for a ``StringIO`` is the way
    to read what the developer would actually have seen, and it also pins that
    the real ``die`` — not a stand-in for it — is what runs.
    """
    calls: list[tuple[str, int]] = []
    printed = io.StringIO()

    def recording_die(msg, code=1):
        calls.append((msg, code))
        rich_console.Console(file=printed, theme=_THEME).print(
            f"[error]ERR[/error] {msg}", highlight=False
        )
        raise SystemExit(code)

    # Both terminal entry points that re-`die()`: `yappy kafka up/down` and the
    # `yappy workflow executor` script.
    monkeypatch.setattr(manager, "die", recording_die)
    monkeypatch.setattr(executor_cli, "die", recording_die)
    return calls, printed


def test_the_command_still_exits_with_the_same_code_and_message(
    tmp_path, monkeypatch, die_calls
):
    """The other half of the contract: `yappy kafka up` must still just fail.

    The real service over a real broken install, reached through the real
    command — not a fake that raises. Same condition the service used to
    ``die()`` on, same exit code, same line on stderr.
    """
    calls, printed = die_calls
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(manager, "_service", lambda: _service(tmp_path, props=False))

    with pytest.raises(SystemExit) as excinfo:
        manager.up("server", detach=True, quiet_deprecation=True)

    assert excinfo.value.code == 1
    (msg, code), = calls
    assert code == 1
    assert msg.startswith("Kafka properties not found")
    assert "Kafka properties not found" in printed.getvalue()


def test_stop_still_exits_the_way_it_did(tmp_path, monkeypatch, die_calls):
    calls, _ = die_calls
    monkeypatch.setattr(api_kafka.sys, "platform", "linux")
    monkeypatch.setattr(manager, "_service", lambda: _service(tmp_path))

    with pytest.raises(SystemExit) as excinfo:
        manager.down("server", quiet_deprecation=True)

    assert excinfo.value.code == 1
    assert calls == [("Kafka management currently supports Windows only", 1)]


def test_an_unknown_target_is_still_refused_before_the_service(monkeypatch, die_calls):
    """The command's own guard, untouched: `down` never reaches the service."""
    calls, _ = die_calls
    reached = []
    monkeypatch.setattr(manager, "_service", lambda: reached.append(1))

    with pytest.raises(SystemExit):
        manager.down("nope", quiet_deprecation=True)

    assert reached == []
    assert calls == [("Invalid target. Use: server or ui", 1)]


def test_an_unknown_action_is_still_refused_before_the_service(monkeypatch, die_calls):
    calls, _ = die_calls
    reached = []
    monkeypatch.setattr(manager, "_service", lambda: reached.append(1))

    with pytest.raises(SystemExit):
        manager.up("serverr", quiet_deprecation=True)

    assert reached == []
    assert calls == [("Invalid action. Use: server, ui, or clean", 1)]


def test_the_executor_script_still_exits(tmp_path, monkeypatch, die_calls):
    """`yappy workflow executor` is a terminal script; it dies like one."""
    calls, _ = die_calls
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    broken = _service(tmp_path, props=False)

    class FakeForward:
        def pf(self, **kwargs):
            return object()

    class FakeSession:
        multiple = FakeForward()
        bastion = FakeForward()

        def start(self):
            return self

        def database(self):
            return SimpleNamespace(port=8100)

        def cleanup(self):
            pass

    monkeypatch.setattr(executor_cli, "Session", lambda env: FakeSession())
    monkeypatch.setattr(
        executor_cli, "DevUtils",
        lambda: SimpleNamespace(kafka=lambda: broken),
    )

    with pytest.raises(SystemExit) as excinfo:
        executor_cli.executor("dev")

    assert excinfo.value.code == 1
    (msg, code), = calls
    assert code == 1
    assert msg.startswith("Kafka properties not found")


# --- the working path did not move ---------------------------------------------


def test_a_healthy_start_is_untouched(tmp_path, tracker, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka.time, "sleep", lambda s: None)
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: False)
    spawned = []
    monkeypatch.setattr(
        KafkaService, "_spawn_detached",
        lambda self, cmd, target, local_port=None, owner="cli": (
            spawned.append((target, owner)) or SimpleNamespace(pid=os.getpid())
        ),
    )

    proc = _service(tmp_path).up("server", detach=True, owner=process_tracker.OWNER_WEB)

    assert proc is not None
    assert spawned == [("server", process_tracker.OWNER_WEB)]


def test_the_tracked_entry_of_a_healthy_start_is_still_written(tmp_path, tracker, monkeypatch):
    """`up()` still tracks what it spawns — the refusal path replaced nothing.

    Patched one level below ``_spawn_detached`` (the real ``Popen``) on purpose:
    the tracking lives *inside* ``_spawn_detached``, so replacing that method
    would have replaced the very thing this test claims to check.
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka.time, "sleep", lambda s: None)
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: False)
    monkeypatch.setattr(
        api_kafka.subprocess, "Popen",
        lambda *a, **k: SimpleNamespace(pid=os.getpid(), poll=lambda: None),
    )

    _service(tmp_path).up("server", detach=True, owner=process_tracker.OWNER_WEB)

    entry = next(
        e for e in process_tracker.get_tracked_processes()
        if e["pid"] == os.getpid() and e["resource"] == "kafka"
    )
    assert entry["owner"] == process_tracker.OWNER_WEB
    assert entry["local_port"] == 9092


# --- a second broker is what corrupts the storage --------------------------------


def test_no_se_puede_levantar_un_segundo_broker(tmp_path, tracker, monkeypatch):
    """El motivo de la falla que se repite: dos brokers sobre el mismo storage.

    El segundo JVM abre el mismo ``log.dirs`` y su recuperación borra lo que el
    primero está escribiendo —"Found invalid offset during recovery. Deleting
    the corrupt segment"— y después se muere con un NPE porque el índice que
    acaba de crear está vacío. El storage queda roto y **todos** los arranques
    siguientes fallan igual, hasta que alguien lo borra a mano.

    Por eso el registro alcanza: el broker que ya está vivo está trackeado, y el
    que está por bindear el puerto también.
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: False)
    process_tracker.track_process(
        os.getpid(), "kafka", target="server", owner=process_tracker.OWNER_WEB,
    )

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path).up("server", detach=True, owner=process_tracker.OWNER_WEB)

    assert "segundo" in excinfo.value.reason


def test_un_puerto_ocupado_tambien_lo_impide(tmp_path, tracker, monkeypatch):
    """El puerto alcanza cuando el tracker no sabe del broker.

    Alguien puede haber levantado el broker a mano —con otra herramienta o con
    otra versión de esta— y el tracker no lo tiene. El puerto sí.
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: True)

    with pytest.raises(KafkaUnavailable) as excinfo:
        _service(tmp_path).up("server", detach=True, owner=process_tracker.OWNER_WEB)

    assert "9092" in excinfo.value.reason


def test_la_web_convierte_el_segundo_broker_en_un_409(tmp_path, tracker, monkeypatch):
    """La fila dice por qué no se levantó, en vez de morirse en silencio.

    Es la misma forma de respuesta que el resto de los refusals: el endpoint
    contesta 409 con el motivo y la fila lo muestra.
    """
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: True)
    monkeypatch.setattr(actions, "Config", lambda: _fake_cfg(tmp_path))
    monkeypatch.setattr(api_kafka, "KafkaService", lambda *a, **k: _service(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/api/workflow/resources/start?env=dev", json={"key": "kafka-server"}
        )

    assert response.status_code == 409
    assert "9092" in response.json()["detail"]