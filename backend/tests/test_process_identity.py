"""The kill guard: what counts as "ours" before ``taskkill /t`` runs.

The guard this file pins down has two halves that are easy to confuse. One is
*containment* — do not stop something that contains us. The other is *identity*
— do not stop a pid that has been recycled into a different program. The bug
that motivated all of it was in the first half and looked like the second: the
ancestor walk used ``os.getppid()``, which takes no arguments and answers for
*us* only, so the loop ran twice and the "family" was always ``{self, parent}``.
A guard that checks two pids is not a guard, and the test that would have said
so did not exist.

The chain walking is therefore tested twice over: as pure functions over an
injected map, where a cycle or a five-deep chain costs nothing to build, and as
the real thing on this machine, where it has to agree with the OS.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

import yappy_library.adapters.processes as base
from yappy_library.adapters import process_identity, process_tracker
from yappy_library.adapters.processes import BaseCommand


# --------------------------------------------------------------------------
# Pure: the chain walk, over a map nobody had to spawn.
# --------------------------------------------------------------------------


def test_ancestors_of_a_toplevel_process_is_itself():
    assert process_identity.ancestors(10, parent_map={10: 0}) == {10}


def test_ancestors_walks_the_whole_chain():
    mapping = {30: 20, 20: 10, 10: 0}
    assert process_identity.ancestors(30, parent_map=mapping) == {30, 20, 10}


def test_ancestors_terminates_on_a_recycled_pid_cycle():
    """A pid read mid-reuse can genuinely point back up the chain. A guard that
    loops here is a hung stop button, not a safe one."""
    mapping = {1: 2, 2: 3, 3: 1}
    assert process_identity.ancestors(1, parent_map=mapping) == {1, 2, 3}


def test_ancestors_terminates_when_a_process_is_its_own_parent():
    assert process_identity.ancestors(7, parent_map={7: 7}) == {7}


def test_ancestors_of_a_pid_the_map_never_saw_is_just_itself():
    """An unknown pid has no known ancestors. That is not the same as "it is
    safe": callers combine it with the containment check, they do not read it as
    a clearance."""
    assert process_identity.ancestors(999, parent_map={}) == {999}


def test_ancestors_stops_at_the_limit():
    mapping = {p: p + 1 for p in range(1, 500)}
    assert process_identity.ancestors(1, parent_map=mapping, limit=5) == {1, 2, 3, 4, 5}


def test_ancestors_of_nothing_is_nothing():
    assert process_identity.ancestors(0, parent_map={1: 0}) == set()


def test_a_process_is_its_own_ancestor():
    """``taskkill /t`` on our own pid kills us exactly as dead as on our parent's.
    A containment test that left self out would wave that straight through."""
    mapping = {5: 0}
    assert process_identity.is_descendant(5, 5, parent_map=mapping) is True


def test_a_grandparent_is_an_ancestor():
    mapping = {30: 20, 20: 10, 10: 0}
    assert process_identity.is_descendant(30, 10, parent_map=mapping) is True
    assert process_identity.is_descendant(30, 20, parent_map=mapping) is True


def test_unrelated_processes_are_not_descendants():
    mapping = {30: 20, 20: 10, 10: 0, 77: 88}
    assert process_identity.is_descendant(30, 77, parent_map=mapping) is False
    assert process_identity.is_descendant(77, 30, parent_map=mapping) is False
    # Neither direction, and the shared root (0) does not count: nothing to kill.
    assert process_identity.is_descendant(0, 0, parent_map=mapping) is False


# --------------------------------------------------------------------------
# Platform layer: same questions, asked of the real OS.
# --------------------------------------------------------------------------


def test_identity_of_an_impossible_pid_is_none_and_does_not_raise():
    # 0x7FFFFFFF is above every pid Windows or Linux hands out, so the lookup
    # fails at the OS. It must come back as "no idea", never as an exception:
    # this is called on a path that is deciding whether to kill something.
    assert process_identity.identity(2147483647) is None


def test_identity_of_zero_is_none():
    assert process_identity.identity(0) is None


def test_the_linux_stat_parser_agrees_with_the_documented_field_numbers():
    """Fields 4 and 22 are the ones this module needs (ppid, starttime), and
    getting the index wrong shifts them by one and produces a parent chain that
    is plausible and completely fictional. Pure so it can be checked from a
    machine that has no ``/proc`` at all."""
    # Built from a field list rather than a literal so the two fields under
    # test cannot drift away from the positions the parser indexes.
    stat = ["1234", "(my proc (x) weird)"] + [str(n) for n in range(1, 30)]
    stat[3] = "1235"      # field 4, ppid
    stat[21] = "987654"   # field 22, starttime
    raw = " ".join(stat)

    fields = process_identity.parse_stat_line(raw)

    assert fields is not None
    assert fields[1] == "my proc (x) weird"
    assert fields[2] == "1"
    assert fields[3] == "1235"
    assert fields[21] == "987654"


def test_the_linux_stat_parser_rejects_a_line_it_cannot_read():
    assert process_identity.parse_stat_line("") is None
    assert process_identity.parse_stat_line("1234 no-parens S 1") is None


def test_the_live_parent_map_agrees_with_getppid():
    """``os.getppid()`` is right about exactly one pid. If the map does not
    contain our own parent, the map is broken and every ancestor check built on
    it is worse than useless — it would answer "safe" about our own parent."""
    mapping = process_identity.parent_map()
    assert mapping, "the platform returned no processes at all"
    assert os.getpid() in mapping
    assert mapping[os.getpid()] == os.getppid()


def test_identity_of_ourselves_looks_like_us():
    live = process_identity.identity(os.getpid())
    assert live is not None
    assert live.pid == os.getpid()
    assert live.parent_pid == os.getppid()
    assert live.exe
    # Lowercased on Windows, basename on both. Anything else and a tracker entry
    # written by a different version would never compare equal to itself.
    assert "/" not in live.exe and "\\" not in live.exe


# --------------------------------------------------------------------------
# The regression: _own_family used to be {self, parent}, always.
# --------------------------------------------------------------------------


def _walk_up_longhand(pid: int, mapping: dict[int, int]) -> set[int]:
    """The expected chain, written out rather than delegated.

    Calling ``process_identity.ancestors`` here would make the test assert that
    the guard and the library agree, which is true even when both are wrong.
    """
    chain: set[int] = set()
    current = pid
    while current and current not in chain:
        chain.add(current)
        current = mapping.get(current, 0)
    return chain


def test_own_family_is_the_real_ancestor_chain():
    mapping = process_identity.parent_map()
    family = BaseCommand._own_family()

    print(f"\nreal chain  : {sorted(_walk_up_longhand(os.getpid(), mapping))}")
    print(f"_own_family : {sorted(family)}")

    assert family == _walk_up_longhand(os.getpid(), mapping)
    assert os.getpid() in family


def test_own_family_is_more_than_me_and_my_parent():
    """The regression, stated as a number.

    ``os.getppid()`` takes no arguments, so a loop that walks the chain with it
    returns ``{os.getpid(), os.getppid()}`` on every machine, forever, no matter
    how deep the real chain is — and the guard silently stops guarding. Asserting
    "> 2" is what makes that failure impossible to ship again.
    """
    mapping = process_identity.parent_map()
    family = BaseCommand._own_family()
    print(f"\nown family : {sorted(family)} ({len(family)} pids)")
    assert len(family) > 2, (
        f"_own_family() devolvió {sorted(family)}: sólo el proceso y su padre. "
        "La caminata volvió a usar os.getppid(), que no toma argumentos."
    )


def test_own_family_includes_the_grandparent_not_only_the_parent():
    mapping = process_identity.parent_map()
    me = os.getpid()
    parent = mapping.get(me)
    grandparent = mapping.get(parent) if parent else None
    if not grandparent:
        pytest.skip("this process has no grandparent on this machine")

    assert grandparent in BaseCommand._own_family()


def test_own_family_falls_back_to_two_pids_when_the_platform_will_not_answer(monkeypatch, capsys):
    """Degrading to the old, broken behaviour beats refusing to stop anything at
    all — but it has to be visible, because a user whose guard is degraded
    deserves to know why."""
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: {})

    family = BaseCommand._own_family()

    assert family == {os.getpid(), os.getppid()}
    assert "mapa de procesos" in capsys.readouterr().out


# --------------------------------------------------------------------------
# _kill_pid: the three gates.
# --------------------------------------------------------------------------


def _win_recorder(monkeypatch):
    """Capture taskkill invocations and make the pid a foreign one.

    Every test here drives the real process table through the real guard, so the
    target pid is chosen out of the map: a pid the fake map does not place in
    our chain cannot be an ancestor of us, whatever the OS says.
    """
    calls = []
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda cmd, *a, **k: (calls.append(cmd), SimpleNamespace(returncode=0))[1],
    )
    monkeypatch.setattr(base.sys, "platform", "win32")
    return calls


def _foreign_map(*extra: tuple[int, int]) -> dict[int, int]:
    mapping = {os.getpid(): os.getppid(), os.getppid(): 0}
    mapping.update(dict(extra))
    return mapping


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    """Point the tracker at a temp dir with an empty parse cache.

    Same fixture as ``test_process_tracker_schema``, duplicated rather than
    imported: a test module that borrows another module's fixtures couples them, and
    this one is about the identity layer, not about the entry format.
    """
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


def _fake_identity(**kwargs):
    defaults = {"pid": 4242, "parent_pid": 1, "exe": "aws.exe", "create_time": 1_700_000_000.0}
    defaults.update(kwargs)
    return process_identity.ProcessIdentity(**defaults)


def test_kill_pid_refuses_an_ancestor_two_levels_up(monkeypatch):
    """The bug. With the old walk this pid was invisible to the guard, so
    ``taskkill /t`` walked straight through the web server to reach it."""
    calls = _win_recorder(monkeypatch)
    me, parent, grandparent = os.getpid(), os.getppid(), 55555
    monkeypatch.setattr(
        base.process_identity,
        "parent_map",
        lambda: {me: parent, parent: grandparent, grandparent: 0},
    )
    monkeypatch.setattr(
        base.process_identity, "identity", lambda pid: _fake_identity(pid=grandparent)
    )

    killed = BaseCommand()._kill_pid(grandparent)

    assert killed is False
    assert calls == []


def test_kill_pid_refuses_a_pid_whose_exe_is_not_the_one_we_tracked(monkeypatch):
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())
    monkeypatch.setattr(
        base.process_identity,
        "identity",
        lambda pid: _fake_identity(pid=4242, exe="powershell.exe"),
    )

    killed = BaseCommand()._kill_pid(4242, expected_exe="aws.exe")

    assert killed is False
    assert calls == []
    assert all("4242" not in cmd for cmd in calls)


def test_kill_pid_refuses_a_pid_that_was_recycled_into_another_process(monkeypatch):
    """Same exe, different incarnation. The pid alone cannot tell them apart;
    that is what the creation time is for."""
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())
    monkeypatch.setattr(
        base.process_identity,
        "identity",
        lambda pid: _fake_identity(pid=4242, create_time=1_700_000_000.0),
    )

    killed = BaseCommand()._kill_pid(4242, expected_exe="aws.exe", expected_create_time=1_600_000_000.0)

    assert killed is False
    assert calls == []


def test_kill_pid_kills_when_the_identity_matches(monkeypatch):
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())
    monkeypatch.setattr(
        base.process_identity,
        "identity",
        lambda pid: _fake_identity(pid=4242, create_time=1_700_000_001.0),
    )

    killed = BaseCommand()._kill_pid(
        4242, expected_exe="aws.exe", expected_create_time=1_700_000_000.0
    )

    assert killed is True
    assert calls == [["taskkill", "/pid", "4242", "/t", "/f"]]


def test_kill_pid_tolerates_a_create_time_that_moved_within_the_precision(monkeypatch):
    """One and a half seconds of drift is rounding, not a recycled pid. A strict
    ``!=`` here would refuse to stop live tunnels."""
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())
    monkeypatch.setattr(
        base.process_identity,
        "identity",
        lambda pid: _fake_identity(pid=4242, create_time=1_700_000_001.5),
    )

    killed = BaseCommand()._kill_pid(4242, expected_exe="aws.exe", expected_create_time=1_700_000_000.0)

    assert killed is True
    assert calls == [["taskkill", "/pid", "4242", "/t", "/f"]]


def test_kill_pid_still_kills_an_entry_with_no_recorded_identity(monkeypatch):
    """Backwards compatibility is the requirement, not a nicety.

    Tracker files outlive the code that wrote them, so entries from before the
    identity fields existed are the *normal* case on a developer machine that
    has been running this tool for a month. Refusing those would mean every
    existing tunnel becomes un-stoppable from the UI.
    """
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())
    monkeypatch.setattr(base.process_identity, "identity", lambda pid: None)

    killed = BaseCommand()._kill_pid(4242, expected_exe=None, expected_create_time=None)

    assert killed is True
    assert calls == [["taskkill", "/pid", "4242", "/t", "/f"]]


def test_an_unidentified_pid_is_only_guarded_by_containment(monkeypatch):
    """A pid we cannot describe is not a licence to kill, but it is also not a
    reason to refuse — unless it contains us."""
    calls = _win_recorder(monkeypatch)
    me, parent = os.getpid(), os.getppid()
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: {me: parent, parent: 0})
    monkeypatch.setattr(base.process_identity, "identity", lambda pid: None)

    killed = BaseCommand()._kill_pid(parent)

    assert killed is False
    assert calls == []


def test_kill_pid_of_nothing_does_nothing(monkeypatch):
    calls = _win_recorder(monkeypatch)
    monkeypatch.setattr(base.process_identity, "parent_map", lambda: _foreign_map())

    assert BaseCommand()._kill_pid(0) is False
    assert calls == []


# --------------------------------------------------------------------------
# kill_ssm passes the tracker's claim through to the guard.
# --------------------------------------------------------------------------


def test_kill_ssm_hands_the_tracked_identity_to_the_guard(monkeypatch):
    """The guard is only as good as what it is told. If the tracker's recorded
    identity never reaches it, everything above is decoration."""
    seen = {}
    monkeypatch.setattr(
        base.process_identity, "parent_map", lambda: _foreign_map()
    )
    monkeypatch.setattr(base.process_identity, "identity", lambda pid: None)
    monkeypatch.setattr(
        base.process_tracker,
        "get_tracked_processes",
        lambda **kw: [{"pid": 4242, "exe": "aws.exe", "create_time": 1_700_000_000.0}],
    )
    monkeypatch.setattr(base.process_tracker, "untrack_process", lambda pid: None)

    def _record(self, pid, **kwargs):
        seen.update({"pid": pid, **kwargs})

    monkeypatch.setattr(BaseCommand, "_kill_pid", _record)

    BaseCommand().kill_ssm(pid=4242)

    assert seen == {
        "pid": 4242,
        "expected_exe": "aws.exe",
        "expected_create_time": 1_700_000_000.0,
    }


def test_kill_ssm_with_no_tracked_identity_passes_nones(monkeypatch):
    """An old tracker file has neither field. The guard has to be told that
    explicitly rather than being handed a missing key."""
    seen = {}
    monkeypatch.setattr(
        base.process_tracker,
        "get_tracked_processes",
        lambda **kw: [{"pid": 4242, "resource": "tunnel"}],
    )
    monkeypatch.setattr(base.process_tracker, "untrack_process", lambda pid: None)
    monkeypatch.setattr(BaseCommand, "_kill_pid", lambda self, pid, **kw: seen.update(kw))

    BaseCommand().kill_ssm(pid=4242)

    assert seen == {"expected_exe": None, "expected_create_time": None}


# --------------------------------------------------------------------------
# Structure of the Windows declaration, which is wrong silently if it is wrong.
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="ctypes.wintypes is Windows-only")
def test_the_toolhelp_entry_matches_the_abi_the_api_expects():
    """A ``PROCESSENTRY32W`` declared with a 32-bit ``ULONG_PTR`` on a 64-bit
    build shifts every field after it by 8 bytes, and ``Process32FirstW`` does
    not fail — it fills the shifted fields with whatever was in memory. Parents
    come back as plausible garbage and the guard waves through a kill."""
    api = process_identity._windows_api()
    assert api is not None
    entry = api["entry"]()
    assert entry.th32ProcessID == 0
    # dwSize is read back by the API to validate the layout; on 64-bit the
    # struct is 568 bytes, on 32-bit 556.
    assert entry.dwSize == 0
    import ctypes

    assert ctypes.sizeof(entry) == (568 if ctypes.sizeof(ctypes.c_void_p) == 8 else 556)


@pytest.mark.skipif(sys.platform != "win32", reason="ctypes.wintypes is Windows-only")
def test_the_windows_snapshot_reports_real_exes_and_parents():
    entries = process_identity._windows_entries()
    assert entries
    me = os.getpid()
    assert me in entries
    assert entries[me][0] == os.getppid()
    assert entries[me][1].endswith(".exe")
    assert entries[me][1] == entries[me][1].lower()

# --------------------------------------------------------------------------
# Liveness: the same "do not trust the pid" mistake, one layer over.
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="la tabla de procesos es Windows-only")
def test_una_lectura_del_tracker_toma_la_tabla_de_procesos_una_sola_vez(
    tracker, monkeypatch
):
    """El guard de :func:`process_identity.alive_map`.

    En Windows la liveness de un pid sale de ``CreateToolhelp32Snapshot`` más un
    recorrido de la tabla completa de procesos, que medido en esta máquina dio
    5,4ms. Preguntar pid por pid —un ``alive`` por entrada y otro por su
    supervisor— hacía que una lectura de cinco filas caminara la tabla diez veces:
    62ms de CPU para responder "no cambió nada". Este test cuenta las caminatas, así
    que volver a preguntar de a uno sale rojo en vez de volver a ser lento en
    silencio.
    """
    walks: list[int] = []
    real_entries = process_identity._windows_entries

    def counting_entries():
        walks.append(1)
        return real_entries()

    monkeypatch.setattr(process_identity, "_windows_entries", counting_entries)

    live = os.getpid()
    tracker.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (tracker / f"{9000 + i}.json").write_text(
            json.dumps(
                {"pid": live, "resource": "tunnel", "state": "up", "supervisor": live}
            )
        )

    rows = process_tracker.get_tracked_processes()

    assert len(rows) == 5
    assert all(r["alive"] and r["supervisor_alive"] for r in rows)
    assert len(walks) == 1, f"caminó la tabla {len(walks)} veces; tiene que ser una"


def test_una_tabla_que_no_se_puede_leer_no_declara_muerto_a_todo(tracker, monkeypatch):
    """Sin tabla no hay respuesta, y "no lo sé" no es "está muerto".

    :func:`alive_map` devuelve ``None`` cuando la API de Windows no está o el
    snapshot viene vacío, y el tracker cae al camino pid por pid. Si ese ``None``
    se leyera como "todos muertos", un fallo transitorio de ctypes pintaría de rojo
    cada fila viva y "Parar todo" se las saltearía — el peor resultado posible a
    partir de una duda.
    """
    tracker.mkdir(parents=True, exist_ok=True)
    (tracker / "9000.json").write_text(
        json.dumps({"pid": 4242, "resource": "tunnel", "state": "up", "supervisor": 4243})
    )

    asked: list[int] = []
    monkeypatch.setattr(process_identity, "alive_map", lambda pids: None)
    monkeypatch.setattr(process_tracker, "_process_alive", lambda pid: asked.append(pid) or True)

    rows = process_tracker.get_tracked_processes()

    assert all(r["alive"] and r["supervisor_alive"] for r in rows)
    assert sorted(asked) == [4242, 4243], "no preguntó de a uno: ése es el camino de reserva"


def test_liveness_does_not_ask_for_control_of_the_process(monkeypatch):
    """``os.kill(pid, 0)`` is not a liveness probe on Windows.

    CPython implements it as ``OpenProcess(PROCESS_ALL_ACCESS)`` — a demand for
    full control — so anything the caller does not fully own raises
    ``PermissionError`` and reads as dead. A live tunnel then shows as ``dead``
    on the screen, ``Parar todo`` skips it, and the entry never goes away.
    """
    seen: list[int] = []

    def fake_identity(pid):
        seen.append(pid)
        return process_identity.ProcessIdentity(pid, 1, "java.exe", 123.0)

    monkeypatch.setattr(process_tracker.sys, "platform", "win32")
    monkeypatch.setattr(process_tracker, "process_identity", SimpleNamespace(identity=fake_identity))

    assert process_tracker._process_alive(4242) is True
    assert seen == [4242]


def test_a_process_nobody_can_open_is_not_alive(monkeypatch):
    monkeypatch.setattr(process_tracker.sys, "platform", "win32")
    monkeypatch.setattr(
        process_tracker, "process_identity", SimpleNamespace(identity=lambda pid: None)
    )

    assert process_tracker._process_alive(4242) is False


@pytest.mark.skipif(sys.platform == "win32", reason="posix branch only")
def test_permission_denied_on_posix_means_the_process_is_there(monkeypatch):
    """On POSIX ``EPERM`` says "it exists, you just may not signal it"."""
    monkeypatch.setattr(process_tracker.sys, "platform", "linux")

    def denied(pid, sig):
        raise PermissionError("EPERM")

    monkeypatch.setattr(process_tracker.os, "kill", denied)
    assert process_tracker._process_alive(4242) is True
