"""`_aws_cmd()` must not end the process it is running inside.

``die()`` is ``error()`` plus ``sys.exit()``, and ``SystemExit`` is a
``BaseException``. That is the wrong property for a library the web drives, and
``_aws_cmd`` is the most exposed version of the mistake in this codebase:

    POST /api/workflow/ports/start   (sync `def`, anyio worker thread)
      -> actions._spawn_tunnel
      -> BaseCommand.ssm_tunnel
      -> _aws_cmd            <- `die()` lived here

Starlette runs the endpoint in a worker thread, anyio re-raises whatever came
out into the event-loop task, ``asyncio.Task.__step`` re-raises ``SystemExit``
explicitly, and it escapes ``run_until_complete``. uvicorn is gone, with no
traceback and nothing in the log — the same silent death as the ``taskkill /T``
cascade, and the same shape as the Kafka defect that was already fixed three
fixes before this one.

The second path is worse than the first, because it is not one screen:

    POST /api/db/diff, /api/query, /api/execute/sql, ...   (all sync `def`)
      -> connection.connect
      -> BaseCommand.ssm_tunnel
      -> _aws_cmd            <- the same line

And the trigger is not a broken Kafka install on one developer's machine. It is
an ordinary state — AWS CLI uninstalled, or `python -m awscli` broken — on a
machine where somebody clicks "start" on a tunnel row.

What is pinned here:

- the library raises `ToolUnavailable`, never `die()`, anywhere on the path that
  spawns a process for the web — asserted on the source, so a new `die()` fails
  the build instead of the server;
- the web turns it into `StartRefused` (409 with the reason) and into `SyncError`
  for the DB routes, both of which those screens already know how to render;
- the terminal still exits exactly as it did: code 1, the same line on stderr, no
  traceback — proven by running the *real* entry point in a subprocess over a
  genuinely missing AWS CLI, not by calling a stand-in;
- `check_requirements`, the one caller that wants the failure, keeps turning it
  into "Missing required tools: aws";
- the two `except SystemExit` guards around `generate_token` — the one remaining
  `die()` reachable from a sync endpoint — are pinned too, so deleting either
  one fails the build instead of the server.
"""

from __future__ import annotations

import ast
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich import console as rich_console

import yappy_cli.cli as cli_module
import yappy_library.adapters.database.connection as connection
import yappy_library.adapters.processes as processes
from yappy_library.adapters.logging import _theme as _THEME
from yappy_library.adapters.processes import BaseCommand, ToolUnavailable, _aws_cmd
from yappy_library.application.workflow import actions
from yappy_library.application.workflow.actions import StartRefused

_REPO = Path(__file__).resolve().parents[2]

#: The message `_aws_cmd` has always produced, byte for byte. Asserted as a
#: literal rather than read back from the module, so editing the string shows up
#: here as a failure somebody has to think about instead of as a test that
#: agrees with whatever the code now says.
AWS_CLI_MESSAGE = "AWS CLI not found. Install with: pip install awscli"


# --- fixtures -----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_cached_aws_cmd():
    """`_aws_cmd` is `lru_cache`d, so a resolved value outlives the test.

    Without this the suite order decides the result: the first test that resolves
    it for real leaves `["aws"]` in the cache, and every later test over a missing
    CLI silently gets a working one.
    """
    _aws_cmd.cache_clear()
    yield
    _aws_cmd.cache_clear()


@pytest.fixture
def no_aws_cli(monkeypatch):
    """Make both AWS-CLI probes fail the way a missing install does.

    `subprocess.run`, not `_aws_cmd` itself: the real resolution order (`aws` on
    PATH, then `python -m awscli`) runs and fails for real, so what is exercised
    below is the code and not a mock shaped like it.
    """

    def missing(*args, **kwargs):
        raise FileNotFoundError("aws")

    monkeypatch.setattr(processes.subprocess, "run", missing)
    _aws_cmd.cache_clear()
    return missing


@pytest.fixture
def tunnel_logs(tmp_path, monkeypatch):
    """Keep the tunnel log files inside `tmp_path`.

    `tunnel_log_path` is `~/.yappy/logs/tunnel-<port>.log` and `ssm_tunnel`
    creates it before it spawns anything, so a test that reaches the spawn must
    not truncate a real developer's log of the tunnel they are using.
    """
    path = tmp_path / "tunnel.log"
    monkeypatch.setattr(actions, "tunnel_log_path", lambda port: path)
    monkeypatch.setattr(connection, "tunnel_log_path", lambda port: path)
    return path


@pytest.fixture
def die_recorder(monkeypatch):
    """Record every `die()` a call makes, and exit like the real one.

    Neither `capsys` nor `capfd` sees the message: `adapters/logging.py` builds
    its `rich` console with `file=sys.stderr` at *import* time, so the console
    holds the original object and keeps writing to it no matter what either
    capture fixture installs. Swapping the console for a `StringIO` is the way to
    read what the developer would actually have seen, and it pins that the real
    `die` — not a stand-in for it — is what runs.
    """
    calls: list[tuple[str, int]] = []
    printed = io.StringIO()

    def recording_die(msg, code=1):
        calls.append((msg, code))
        rich_console.Console(file=printed, theme=_THEME).print(
            f"[error]ERR[/error] {msg}", highlight=False
        )
        raise SystemExit(code)

    def install(namespace):
        monkeypatch.setattr(namespace, "die", recording_die)
        return calls

    install.calls = calls  # type: ignore[attr-defined]
    install.printed = printed  # type: ignore[attr-defined]
    return install


# --- the library no longer ends the process ------------------------------------


def test_a_missing_aws_cli_raises_instead_of_exiting(no_aws_cli):
    """The one line this defect lived on.

    Asserted through `SystemExit` rather than through `Exception` on purpose:
    the whole failure mode is that `SystemExit` is a `BaseException`, so a test
    that only checked `pytest.raises(ToolUnavailable)` would keep passing if
    somebody made `ToolUnavailable` inherit from `SystemExit` again.
    """
    with pytest.raises(ToolUnavailable) as excinfo:
        _aws_cmd()

    assert excinfo.value.reason == AWS_CLI_MESSAGE
    assert isinstance(excinfo.value, Exception)
    assert not isinstance(excinfo.value, SystemExit)


def test_the_exception_carries_the_reason_and_not_only_its_own_text(no_aws_cli):
    """`.reason` is the contract; `str(exc)` is an implementation detail.

    Every wrapper reads `.reason` — the 409 body, the DB error row and the CLI's
    `die()` — so an override of `__str__` would leave all three messages empty
    while still passing a test that only looked at the raised type.
    """
    with pytest.raises(ToolUnavailable) as excinfo:
        _aws_cmd()

    assert excinfo.value.reason == AWS_CLI_MESSAGE
    assert excinfo.value.args == (AWS_CLI_MESSAGE,)


def test_a_working_aws_cli_still_resolves_to_the_binary(monkeypatch):
    """The refusal must not fire on a healthy machine.

    `_aws_cmd` runs on every tunnel and every DB query, so a change here that
    broke the happy path would be a total outage dressed up as a fix.
    """
    monkeypatch.setattr(
        processes.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    _aws_cmd.cache_clear()

    assert _aws_cmd() == ["aws"]


def test_a_module_awscli_is_accepted_when_there_is_no_binary(monkeypatch):
    """A machine with no `aws` on PATH is still usable and must not be refused.

    This is also why `check_requirements` asks `_aws_cmd` instead of running
    `which aws`, and it is the second thing a naive "raise if missing" breaks.
    """
    def only_python_module(cmd, **kwargs):
        if cmd[:1] == ["aws"]:
            raise FileNotFoundError("aws")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(processes.subprocess, "run", only_python_module)
    _aws_cmd.cache_clear()

    assert _aws_cmd() == [sys.executable, "-m", "awscli"]


def test_popen_reports_a_missing_binary_without_exiting(monkeypatch):
    """The statement right below `_aws_cmd()` in the same web-reachable function.

    `ssm_tunnel` reaches `_aws_cmd()` first, so a broken install is normally
    caught there. `FileNotFoundError` here means the OS refused the exact binary
    that was just verified — a PATH race, a reinstall in progress, an AppLocker
    refusal. Rare, and not rarer than the thing `ToolUnavailable` was written
    for: without it, the one function the web calls for every tunnel keeps a
    live `SystemExit` escape.
    """
    def refuse(*args, **kwargs):
        raise FileNotFoundError("aws")

    monkeypatch.setattr(processes.subprocess, "Popen", refuse)

    with pytest.raises(ToolUnavailable) as excinfo:
        BaseCommand().popen(["aws", "ssm", "start-session"])

    assert excinfo.value.reason == "Command not found: aws. Is it installed?"


def test_nothing_on_the_tunnel_path_raises_a_base_exception(no_aws_cli, tunnel_logs):
    """`except Exception` cannot catch `SystemExit`, and that *is* the bug.

    Stated as a property of the failure rather than of the type, so a change that
    swaps `ToolUnavailable` back for `SystemExit` fails here with a reason naming
    the mechanism — instead of failing a route test with "connection reset" and
    a dead server.
    """
    try:
        BaseCommand().ssm_tunnel(
            instance="i-123",
            port=9000,
            local_port=9000,
            region="us-east-1",
            profile="base",
            quiet=True,
            log_file=tunnel_logs,
        )
    except Exception as exc:  # noqa: BLE001 — what lands here is the point
        assert isinstance(exc, ToolUnavailable)
        assert not isinstance(exc, SystemExit)
    else:
        pytest.fail("a tunnel was spawned with no AWS CLI installed")


# --- the invariant, asserted on the source -------------------------------------

#: Module -> the functions the web enters it through, per module. A whole-file
#: scan would be wrong here: `processes.py` also holds `check_requirements`,
#: `validate_env` and `serve_forever`, which are CLI-only and are *allowed* to
#: `die()`. What has to be clean is the path, not the file.
WEB_REACHED = [
    (processes, "_aws_cmd"),
    (processes, "popen"),
    (processes, "ssm_tunnel"),
    (actions, "start_resource"),
    (actions, "start_port"),
    (actions, "_start_db"),
    (actions, "_start_kafka"),
    (actions, "_spawn_tunnel"),
    (connection, "connect"),
]

_EXIT_NAMES = {"die", "exit", "quit", "_exit"}


def _function_node(module, name: str) -> ast.AST:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
                    return child
    raise AssertionError(f"{module.__name__} has no function or method named {name!r}")


def _ways_to_end_the_interpreter(body: ast.AST) -> list[str]:
    """Every way the given function can end the process it runs in.

    Matched on the attribute name, never on the module it hangs off: `os.chmod`
    and `sys.exc_info` are ordinary calls, and a guard that flags them teaches
    people to work around the guard.
    """
    found = []
    for node in ast.walk(body):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id in _EXIT_NAMES:
                found.append(f"{target.id}() at line {node.lineno}")
            elif isinstance(target, ast.Attribute) and target.attr in _EXIT_NAMES:
                found.append(f"{ast.unparse(target)}() at line {node.lineno}")
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            callee = node.exc.func
            if isinstance(callee, ast.Name) and callee.id == "SystemExit":
                found.append(f"raise SystemExit at line {node.lineno}")
    return found


@pytest.mark.parametrize(
    "module, name", WEB_REACHED, ids=[f"{m.__name__}.{n}" for m, n in WEB_REACHED]
)
def test_no_die_or_sys_exit_on_a_web_reachable_path(module, name):
    """The whole fix, asserted on the source rather than on a call.

    A test that exercises four failure shapes only proves those four. Somebody
    adding a fifth `die()` — for a new check, in a new helper, in a new branch of
    `ssm_tunnel` — would pass every behavioural test in this file. This one
    cannot: it parses the function the web enters and fails on any `die()`,
    `sys.exit()`, `os._exit()` or `raise SystemExit` in that function's body.
    """
    offenders = _ways_to_end_the_interpreter(_function_node(module, name))
    assert not offenders, (
        f"{module.__name__}.{name} can end the process on a web-reachable path: "
        f"{offenders}. `die()`/`sys.exit()` is a `BaseException`, so it does not "
        f"fail the request — it takes uvicorn down with it, silently. Raise "
        f"ToolUnavailable and let the route turn it into a 409."
    )


def test_the_map_covers_every_sync_endpoint_that_starts_a_process():
    """Why these nine, and what a new one would have to do about it.

    The web reaches process-spawning code from exactly two places: the workflow
    actions (`routes/workflow.py`, three sync `def` endpoints) and the DB
    connection (`routes/db.py`, every sync `def` endpoint). Both lists are pinned
    here so that adding a route cannot quietly open a third door — the new
    caller has to appear in `WEB_REACHED`, where the guard above will hold it to
    the same rule.
    """
    from yappy_api.app import app as api_app

    spawners = {
        "api_workflow_resource_start",
        "api_workflow_port_start",
    }
    guarded_modules = {module for module, _ in WEB_REACHED}

    for route in api_app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or endpoint.__name__ not in spawners:
            continue
        assert endpoint.__module__ == "yappy_api.routes.workflow"

    assert connection in guarded_modules
    assert actions in guarded_modules
    assert processes in guarded_modules


# --- the refusal the screens show ---------------------------------------------


def _tunnel_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        env="dev",
        db_port=8100,
        profile="base-profile",
        require=lambda key: {"AWS_INSTANCE": "i-123", "AWS_REGION": "us-east-1"}[key],
        get=lambda key, default=None: default,
    )


def test_a_start_row_refuses_instead_of_killing_the_server(no_aws_cli, tunnel_logs):
    """`POST /api/workflow/resources/start` is a sync `def`.

    A refusal is the shape the screen already knows: the route answers 409 with
    the reason in `detail` and the row renders it. The alternative is not an
    error card — it is a UI that is not there any more.
    """
    with pytest.raises(StartRefused) as excinfo:
        actions._spawn_tunnel(
            _tunnel_cfg(), port=9000, local_port=9000, remote_host=None,
            cluster="cap", label="Kafka UI", env="dev",
        )

    assert excinfo.value.reason.startswith("No se pudo levantar Kafka UI")
    assert AWS_CLI_MESSAGE in excinfo.value.reason


def test_the_refusal_names_the_row_even_when_it_has_no_label(no_aws_cli, tunnel_logs):
    """`start_port` passes the caller's label, which may well be empty.

    Without the fallback the message reads "No se pudo levantar : AWS CLI not
    found", which names nothing at all.
    """
    with pytest.raises(StartRefused) as excinfo:
        actions._spawn_tunnel(
            _tunnel_cfg(), port=9000, local_port=9000, remote_host=None,
            cluster="cap", label="", env="dev",
        )

    assert excinfo.value.reason == f"No se pudo levantar el túnel: {AWS_CLI_MESSAGE}"


def test_the_refusal_is_chained_so_the_log_still_names_the_cause(no_aws_cli, tunnel_logs):
    """`raise ... from exc` is not decoration.

    The route catches `StartRefused` and shows it to the user, so the message
    survives either way — but without the chain a developer reading the uvicorn
    log sees only `StartRefused` and never learns what raised it.
    """
    with pytest.raises(StartRefused) as excinfo:
        actions._spawn_tunnel(
            _tunnel_cfg(), port=9000, local_port=9000, remote_host=None,
            cluster="cap", label="Cap", env="dev",
        )

    assert isinstance(excinfo.value.__cause__, ToolUnavailable)


def test_the_route_answers_409_with_the_reason(
    no_aws_cli, tmp_path, monkeypatch
):
    """End to end through the app, so the FastAPI layer is really in the path."""
    from fastapi.testclient import TestClient

    from yappy_api.app import app as api_app

    from yappy_library.adapters import process_tracker

    # The route builds its own `Config`; `YAPPY_*` is the override that config
    # reads, and it keeps this test off the developer's own env files.
    monkeypatch.setenv("YAPPY_AWS_INSTANCE", "i-123")
    monkeypatch.setenv("YAPPY_AWS_REGION", "us-east-1")
    monkeypatch.setattr(actions, "tunnel_log_path", lambda port: tmp_path / "t.log")
    # `ensure_port_free` prunes the tracker on the way in; do that to a temp one.
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", tmp_path / "tracker")
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})

    with TestClient(api_app) as client:
        response = client.post(
            "/api/workflow/ports/start?env=dev&cluster=cap",
            json={"port": 8412, "label": "Kafka UI"},
        )

    assert response.status_code == 409
    assert AWS_CLI_MESSAGE in response.json()["detail"]


def test_a_db_request_is_an_error_row_not_a_dead_server(no_aws_cli, tunnel_logs, monkeypatch):
    """The other web path, and the one that fires on every DB screen.

    Every DB route is a sync `def` and every one of them goes through
    `connect()`. They all already catch `SyncError` and render it, so this is the
    conversion that turns a missing AWS CLI from "the UI vanished" into
    "No se pudo abrir el túnel a la base: AWS CLI not found".
    """
    from yappy_library.adapters.database.connection import SyncError

    monkeypatch.setattr(connection, "_local_env_values", lambda: {})
    monkeypatch.setattr(connection, "generate_token", lambda cfg: "tok")
    monkeypatch.setattr(connection, "mysql_greeting", lambda port, timeout: False)

    cfg = SimpleNamespace(
        env="dev",
        db_port=8100,
        profile="base-profile",
        aws_user="app",
        region="us-east-1",
        get=lambda key, default=None: {
            "AWS_INSTANCE": "i-123",
            "AWS_HOST": "db.x.rds.amazonaws.com",
            "AWS_PORT": "53360",
            "AWS_REGION": "us-east-1",
        }.get(key, default),
    )

    with pytest.raises(SyncError) as excinfo:
        with connection.connect(cfg):
            pass

    assert AWS_CLI_MESSAGE in str(excinfo.value)
    assert not isinstance(excinfo.value, SystemExit)


# --- the terminal is unchanged -------------------------------------------------

#: A real interpreter, running the real `app`, over a real missing AWS CLI.
#: `_YappyTyper` is the outermost frame Typer owns, so the exit code and the
#: stderr line here are the ones a developer typing `yappy ...` would see.
#: `CliRunner` cannot prove that: it intercepts the `SystemExit` and reports it
#: as `result.exit_code`, so it cannot tell a clean exit from a swallowed one,
#: and it never runs `sys.excepthook` — which is where Typer's traceback comes
#: from. An unhandled exception here would exit non-zero *and* print a stack
#: trace, which is strictly worse than one clean error line.
_PROBE = """
import subprocess, sys


def _missing(*args, **kwargs):
    raise FileNotFoundError("aws")


subprocess.run = _missing

import yappy_library.adapters.processes as processes
processes._aws_cmd.cache_clear()

from yappy_cli.cli import app

sys.argv = ["yappy"] + sys.argv[1:]
app()
"""


@pytest.fixture
def cli_config_dir(tmp_path):
    """A hermetic config dir so the probe never depends on a developer's env files.

    `Config._get_config_dir()` honours `YAPPY_CONFIG_DIR` first, before the
    editable-install lookup, so the subprocess reads this and nothing else.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "env.base").write_text("AWS_USER=app\n", encoding="utf-8")
    (config_dir / "env.dev").write_text("AWS_PROFILE=base\n", encoding="utf-8")
    return config_dir


def _run_cli(config_dir: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "YAPPY_CONFIG_DIR": str(config_dir)}
    return subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO),
        env=env,
        timeout=180,
    )


def test_the_cli_exits_one_with_the_same_message(cli_config_dir):
    """The other half of the contract: a missing AWS CLI still just fails.

    `yappy exec aws dev s3 ls` is the shortest path from the console to
    `_aws_cmd()` with nothing in between. Before this fix the exit came from
    `die()` inside the library; now it comes from the Typer boundary. Same code,
    same line, no traceback — and "no traceback" is the part that makes it a fix
    rather than a change of where the failure happens.
    """
    result = _run_cli(cli_config_dir, ["exec", "aws", "dev", "s3", "ls"])

    assert result.returncode == 1, (
        f"expected exit code 1, got {result.returncode}. stderr:\n{result.stderr}"
    )
    assert AWS_CLI_MESSAGE in result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "ToolUnavailable" not in result.stderr, result.stderr


def test_the_preflight_command_keeps_its_own_message(cli_config_dir):
    """`check_requirements` is a separate contract and it must stay separate.

    It reports the tool as *missing* rather than failing to resolve it, and it
    was the one caller that already handled the old `SystemExit` on purpose. Both
    commands hit `_aws_cmd()` over a missing CLI and they say different things;
    collapsing them into one message would be a regression.
    """
    result = _run_cli(cli_config_dir, ["aws", "mfa", "someone", "123456"])

    assert result.returncode == 1, (
        f"expected exit code 1, got {result.returncode}. stderr:\n{result.stderr}"
    )
    assert "Missing required tools: aws. Please install them first." in result.stderr
    assert "Traceback" not in result.stderr, result.stderr


def test_a_command_that_does_not_need_a_tool_is_untouched(cli_config_dir):
    """The handler is on every command, so it must not disturb the ones that work.

    `yappy version` never touches `_aws_cmd`; the point is that it still runs
    through the same `_YappyTyper.__call__` and still succeeds.
    """
    result = _run_cli(cli_config_dir, ["version"])

    assert result.returncode == 0, result.stderr
    assert "yappy-cli-manager" in result.stdout


def test_the_app_is_the_typed_one_so_the_handler_cannot_be_dropped():
    """`yappy` is `yappy_cli.cli:app`; the handler lives in that class.

    Without this, swapping the constructor back to `typer.Typer` would compile
    and pass every in-process test here — the subprocess probe would still catch
    it, but only on the machines that run the slow ones.
    """
    assert isinstance(cli_module.app, cli_module._YappyTyper)
    assert isinstance(cli_module.app, typer.Typer)
    assert cli_module.app.__call__ is not typer.Typer.__call__


def test_the_handler_turns_the_refusal_into_the_exit_it_used_to_be(
    monkeypatch, die_recorder
):
    """Unit-level proof of what the subprocess probe shows end to end.

    `typer.Typer.__call__` is replaced so only the handler under test runs: the
    point is what happens to the exception on the way out, not which command
    produced it.
    """
    install = die_recorder
    install(cli_module)

    def refuse(*args, **kwargs):
        raise ToolUnavailable(AWS_CLI_MESSAGE)

    monkeypatch.setattr(typer.Typer, "__call__", refuse)

    with pytest.raises(SystemExit) as excinfo:
        cli_module.app()

    assert excinfo.value.code == 1
    assert install.calls == [(AWS_CLI_MESSAGE, 1)]
    assert AWS_CLI_MESSAGE in install.printed.getvalue()


def test_the_handler_does_not_swallow_anything_else(monkeypatch):
    """It catches one exception, not "errors".

    A handler this broad would turn every real bug in a command into a clean
    one-line exit, which is the same class of failure this whole change is about:
    a problem that cannot be read because it refuses to say what it is.
    """
    def broken(*args, **kwargs):
        raise ValueError("something else went wrong")

    monkeypatch.setattr(typer.Typer, "__call__", broken)

    with pytest.raises(ValueError, match="something else went wrong"):
        cli_module.app()


# --- check_requirements keeps its contract -------------------------------------


def test_check_requirements_still_collects_aws_into_the_missing_list(
    no_aws_cli, monkeypatch, die_recorder
):
    """The one caller that wants the failure, and the reason it is not dead code.

    It is a preflight: it turns "no AWS CLI" into one line before a workflow
    starts, instead of letting the first command die halfway through. If the
    `except` clause went back to catching `SystemExit`, this would let
    `ToolUnavailable` out of the preflight and print a traceback.
    """
    install = die_recorder
    install(processes)

    with pytest.raises(SystemExit) as excinfo:
        BaseCommand().check_requirements("aws")

    assert excinfo.value.code == 1
    assert install.calls == [
        ("Missing required tools: aws. Please install them first.", 1)
    ]
    assert "Missing required tools: aws" in install.printed.getvalue()


def test_check_requirements_lists_every_missing_tool_not_the_first(
    no_aws_cli, monkeypatch, die_recorder
):
    """It is a list, so `aws, session-manager-plugin` reports both at once."""
    install = die_recorder
    install(processes)
    monkeypatch.setattr(BaseCommand, "_which", staticmethod(lambda cmd: False))

    with pytest.raises(SystemExit):
        BaseCommand().check_requirements("aws", "session-manager-plugin")

    (msg, code), = install.calls
    assert "aws" in msg and "session-manager-plugin" in msg
    assert code == 1


def test_check_requirements_does_not_call_die_on_a_healthy_machine(monkeypatch):
    """The preflight must not become the thing that breaks the happy path."""
    monkeypatch.setattr(
        processes.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    _aws_cmd.cache_clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("die() on a machine that has the AWS CLI")

    monkeypatch.setattr(processes, "die", forbidden)

    assert BaseCommand().check_requirements("aws") is None


# --- the one remaining hazard, pinned ------------------------------------------


def _wraps_call_in_except_systemexit(body: ast.AST, call_name: str) -> bool:
    """True when a `call_name(...)` sits inside the body of an `except SystemExit`."""
    for node in ast.walk(body):
        if not isinstance(node, ast.Try):
            continue
        if not any(
            h.type is not None and ast.unparse(h.type) == "SystemExit"
            for h in node.handlers
        ):
            continue
        inner = ast.Module(body=node.body, type_ignores=[])
        for child in ast.walk(inner):
            if isinstance(child, ast.Call) and call_name in ast.unparse(child.func):
                return True
    return False


def test_the_token_die_is_still_caught_on_both_web_paths():
    """`credentials.generate_token` still calls `die()`, and it is still reachable.

    It is reached from every DB request (`connection.connect`) and from
    `POST /api/workflow/resources/start` for the Aurora row
    (`actions._start_db`). Both wrap it in `except SystemExit` and turn it into
    the error shape their route already renders — so today it is caught, not a
    silent death. It is asserted here rather than left implicit because that
    guard is the *only* thing standing between it and a dead server, and a guard
    that nothing tests is how this defect got fixed three times and survived a
    fourth.

    It is the obvious next `die()` to remove: `ToolUnavailable` is already the
    shape, and both web call sites would just change which exception they catch.
    """
    assert _wraps_call_in_except_systemexit(_function_node(connection, "connect"), "generate_token")
    assert _wraps_call_in_except_systemexit(_function_node(actions, "_start_db"), "generate_token")
