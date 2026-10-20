"""Opening a URL must not put a child in the server's console group.

`os.system(f'start "" "{url}"')` hands a string to `system()`, which on Windows
spawns `cmd.exe` **inheriting the caller's console and process group**. That is
precisely the group `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` was introduced
to escape: the broker used to go down with the Ctrl+C that reached the server's
group. A browser window opened from the server's group is the same defect wearing
a different hat — it belongs to the server's console, not to the developer's
session, and nothing about the caller changes that.

There were two live call sites, and neither shared code:

- ``adapters/kafka/service.py::_open_kafdrop_browser`` — gated on owner, so it
  only opened from a terminal, but still joined that terminal's group;
- ``adapters/processes.py::BaseCommand.open_browser`` — no owner check at all.

The fix is one helper (``processes.open_url``) plus one helper for the flags
(``processes.detached_popen_kwargs``), used by both. These tests pin the parts
that break silently:

- the detached flags are actually passed, on the branch taken;
- ``start`` still gets its empty title argument, without which a quoted URL is
  consumed as the window title and nothing opens;
- ``os.system`` does not come back, which no behavioural test can catch.
"""

from __future__ import annotations

import ast
import os
import subprocess
import webbrowser
from pathlib import Path
from types import SimpleNamespace

import pytest

import yappy_library.adapters.kafka.service as api_kafka
from yappy_library.adapters import processes as base
from yappy_library.adapters.kafka.service import KAFDROP_UI_PORT, KafkaService
from yappy_library.adapters.processes import BaseCommand, detached_popen_kwargs, open_url


@pytest.fixture
def spawned(monkeypatch):
    """Record `Popen` calls instead of launching anything."""
    calls: list[tuple[list, dict]] = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(base.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(base.sys, "platform", "win32")
    return calls


def test_a_url_is_opened_out_of_the_callers_console_group(spawned):
    open_url("http://localhost:8080")

    (cmd, kwargs), = spawned
    assert kwargs["creationflags"] == (
        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    assert "start_new_session" not in kwargs


def test_start_still_gets_its_empty_title(spawned):
    """The argument that makes a quoted URL open at all.

    `start` reads its first quoted token as the *window title*. Without the empty
    string, `start "http://localhost:8080"` opens a blank window titled with the
    URL and exits 0 — the browser never appears and nothing reports a failure.
    The old `os.system` string carried it, so the argument list has to.
    """
    open_url("http://localhost:8080")

    (cmd, _), = spawned
    assert cmd == ["cmd", "/c", "start", "", "http://localhost:8080"]


def test_a_url_with_spaces_still_opens(spawned):
    """The reason the URL is one argv element and not split on spaces."""
    open_url("https://localhost:9001/ui?a=1&b=2 3")

    (cmd, _), = spawned
    assert cmd[-1] == "https://localhost:9001/ui?a=1&b=2 3"
    assert cmd[-2] == ""


def test_the_child_gets_no_inherited_stdio(spawned):
    """A detached child still holds our pipes open unless told otherwise.

    `DETACHED_PROCESS` detaches the *console*, not the standard handles. A child
    that inherits the server's stdout keeps the pipe open after the server closes
    it, and anything reading that pipe hangs forever waiting for EOF.
    """
    open_url("http://localhost:8080")

    (_, kwargs), = spawned
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] is subprocess.DEVNULL


def test_a_posix_box_still_uses_the_browser_library(monkeypatch):
    """The Windows branch is new; this one is what it replaced."""
    opened: list[str] = []
    monkeypatch.setattr(base.sys, "platform", "linux")
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))

    open_url("http://localhost:8080")

    assert opened == ["http://localhost:8080"]


def test_base_command_open_browser_goes_through_the_shared_helper(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(base, "open_url", lambda url: seen.append(url))

    BaseCommand().open_browser("http://localhost:9001/")

    assert seen == ["http://localhost:9001/"]


def test_the_kafdrop_opener_goes_through_the_shared_helper(monkeypatch):
    """The second call site: same helper, same answer, owner gate untouched."""
    seen: list[str] = []
    monkeypatch.setattr(api_kafka, "open_url", lambda url: seen.append(url))

    api_kafka._open_kafdrop_browser()

    assert seen == [f"http://localhost:{KAFDROP_UI_PORT}"]


def test_the_kafka_spawn_uses_the_flags_that_let_it_leave_by_itself(
    spawned, monkeypatch, tmp_path
):
    """El broker tiene consola propia; el navegador no.

    La diferencia es deliberada y es lo que hace posible el apagado limpio:
    un ``CTRL_C`` no se puede dirigir a un grupo de procesos, así que el
    broker —al que hay que poder pedirle que salga— tiene que ser el único en
    su consola. Un navegador no tiene nada que cerrar, así que sigue detachado
    como antes.

    Y lo que sigue siendo verdad: los flags no están copiados en el call site,
    cada spawn lee de su helper.
    """
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    monkeypatch.setattr(api_kafka.time, "sleep", lambda s: None)
    # El puerto del broker es el de la máquina: que haya uno corriendo no tiene
    # que ver con los flags del spawn.
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: False)
    calls: list[tuple[list, dict]] = []
    monkeypatch.setattr(
        api_kafka.subprocess, "Popen",
        lambda cmd, **kwargs: (
            calls.append((cmd, kwargs)) or SimpleNamespace(pid=os.getpid())
        ),
    )

    core = tmp_path / "core"
    (core / "config" / "kraft").mkdir(parents=True)
    (core / "config" / "kraft" / "server.properties").write_text(
        "listeners=PLAINTEXT://:9092\n", encoding="utf-8"
    )
    (core / "libs").mkdir()
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "main.jar").write_bytes(b"")
    cfg = SimpleNamespace(
        kafka_core_path=str(core),
        kafka_ui_path=str(ui),
        kafka_path=str(tmp_path / "kafka"),
    )
    KafkaService(cfg).up("server", detach=True)

    open_url("http://localhost:8080")

    broker_cmd, broker_kwargs = calls[0]
    browser_cmd, browser_kwargs = calls[1]

    # El broker tiene consola propia —no DETACHED_PROCESS— y la ventana
    # escondida; y lo lanza un shim, que es lo que rehabilita el Ctrl+C.
    assert broker_kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert not broker_kwargs["creationflags"] & subprocess.DETACHED_PROCESS
    assert broker_kwargs["startupinfo"].wShowWindow == subprocess.SW_HIDE
    assert "SetConsoleCtrlHandler(None, False)" in broker_cmd[3]
    # El navegador sigue como estaba: sin consola.
    assert browser_kwargs["creationflags"] & subprocess.DETACHED_PROCESS


def _backend_sources() -> list[Path]:
    """Every shipped backend module.

    Two directories are excluded on purpose. ``tests/`` because these two scans
    look for a *shape* in production code, and a test that mentions ``os.system``
    or ``DETACHED_PROCESS`` in order to assert their absence is not a violation
    of it. ``build/`` because it is gitignored output of an older tree: a stale
    copy of the bug is not the bug coming back, and a test that fails because of
    it teaches everybody to ignore itself.
    """
    backend = Path(base.__file__).resolve().parents[3]
    skip = {"tests", "build", "__pycache__"}
    return [
        s for s in backend.rglob("*.py")
        if not skip & set(s.relative_to(backend).parts)
    ]


def _sources_using_os_system() -> list[str]:
    """Every backend module that still calls `os.system`.

    Not a test on its own — the assertion below is. But the search is over the
    whole backend rather than the two files that were fixed, because the point is
    that this is the only such call left and nobody has to know where it was.
    """
    backend = Path(base.__file__).resolve().parents[3]
    hits = []
    for source in _backend_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "system"
            ):
                hits.append(source.relative_to(backend).as_posix())
    return hits


def test_os_system_is_gone_from_the_backend():
    """The defect was `os.system` itself, so the assertion is on the call.

    A test that opens a browser proves `open_url` works. It cannot prove the old
    line is not still there somewhere else, and `os.system` in a library is
    exactly the kind of thing that comes back in a "quick fix".
    """
    assert _sources_using_os_system() == []


def test_the_detach_helper_is_the_only_place_the_flags_are_written():
    """One definition, not two that happen to agree today.

    `CREATE_NEW_PROCESS_GROUP` without `DETACHED_PROCESS` still leaves the child
    in our group, and `DETACHED_PROCESS` alone still leaves it in the group. They
    are only correct together, which is the kind of fact that is easy to get
    wrong the second time it is typed out.
    """
    assert detached_popen_kwargs() == {
        "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    }

    backend = Path(base.__file__).resolve().parents[3]
    writers = set()
    for source in _backend_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP")
                and isinstance(node.value, ast.Name)
                and node.value.id == "subprocess"
            ):
                writers.add(source.relative_to(backend).as_posix())
    assert writers == {"library/yappy_library/adapters/processes.py"}


def test_the_detach_helper_speaks_posix_when_there_is_no_windows(monkeypatch):
    monkeypatch.setattr(base.sys, "platform", "linux")

    assert detached_popen_kwargs() == {"start_new_session": True}