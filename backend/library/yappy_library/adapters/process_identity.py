"""What process is this pid, really, and what contains it.

Everything the kill path needs to answer "is this the process we started?" lives
here, behind one platform-neutral surface, because the answer has to come from the
OS and ``os`` does not expose it. The specific hole this fills:

``os.getppid()`` takes no arguments. It answers for *us*, not for an arbitrary
pid, so a loop that walks a parent chain with it reads the same value every
iteration and stops after two steps. Every "is this pid an ancestor of mine?"
guard built that way silently checks only ``{os.getpid(), os.getppid()}`` — which
is how ``taskkill /t`` ends up killing the web server from the web server.

So the parent map is read for real here, per platform, and the interesting part
of the module is written as a **pure function over an injected map**
(:func:`ancestors`, :func:`is_descendant`). That split is deliberate: the chain
walking is the part that has bugs, and it is the part that can be tested against
a fake map without spawning a process tree or asking Windows anything.

Two rules, both learned the hard way:

- **Nothing in here raises.** Every entry point degrades to a safe answer —
  ``None``, ``0.0``, ``{}``, an empty set — because the callers are on a path
  whose job is to *not* kill the wrong thing, and an exception in that path is
  either swallowed (so the kill proceeds unprotected) or fatal (so the UI dies).
  A missing answer has to read as "do not know", never as "it is fine".
- **Creation time is compared, not trusted exactly.** Both sides come from the
  OS, but through different code paths with different precision. It exists to
  distinguish "the pid we started" from "whatever inherited the pid later",
  which is a boolean question, so a tolerance is part of the contract rather
  than a fudge — see :data:`CREATE_TIME_TOLERANCE`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass

#: How many links to follow before giving up. A parent chain is 3-5 deep on any
#: sane machine; a cycle is what this is really for. Pids are recycled, and a
#: map read while a pid is being reused can genuinely contain a loop — the
#: termination condition is "seen it already", not "ran out of links".
DEFAULT_ANCESTOR_LIMIT = 64

#: Seconds of slack when comparing creation times. Both sides come from the OS
#: but not through the same door: one is read at track time and rounded into
#: JSON, the other is read through ``GetProcessTimes``/``/proc`` at kill time.
#: Two seconds is far below any plausible gap between "we started this" and "we
#: stopped this", and far above any rounding either side does.
CREATE_TIME_TOLERANCE = 2.0

#: ``FILETIME`` counts 100-nanosecond intervals since 1601-01-01. The distance
#: from that epoch to the unix one, in the same units.
_FILETIME_UNIX_EPOCH_DELTA = 11644473600.0

#: ``szExeFile`` is ``WCHAR[MAX_PATH]``, and ``MAX_PATH`` is 260 on every
#: Windows version this will ever run on.
_MAX_PATH = 260


@dataclass(frozen=True)
class ProcessIdentity:
    """What the OS says about one pid, at one instant.

    ``create_time`` is the interesting one: a pid on its own says nothing about
    *which* process holds it, because Windows reuses pids and reuses them fast.
    The pair (pid, create_time) is stable for the life of a process and is what
    makes a tracker entry from last week distinguishable from today's shell.
    """

    pid: int
    parent_pid: int
    #: Basename only, lowercased on Windows — the case there is not meaningful
    #: and a tracker entry written by one version must compare equal to one
    #: written by another.
    exe: str
    #: Unix epoch seconds. ``0.0`` when the platform cannot tell, which is a
    #: real answer here (macOS, without shelling out to `sysctl` for every
    #: process) and must never be treated as "created at the epoch".
    create_time: float


# --------------------------------------------------------------------------
# Pure part: chain walking over a map. This is what gets tested.
# --------------------------------------------------------------------------


def ancestors(
    pid: int,
    *,
    limit: int = DEFAULT_ANCESTOR_LIMIT,
    parent_map: dict[int, int] | None = None,
) -> set[int]:
    """``pid`` and every process that contains it, as pids.

    Upwards only, and ``pid`` is always in its own result: a process is its own
    ancestor. That is not a convenience, it is the whole point — ``taskkill /t``
    on a pid that *is* the server kills the server just as dead as one that
    spawned it, and a check that left self out would wave that through.

    ``parent_map`` is a keyword so the tests can walk a chain nobody has to
    spawn, and so a caller that already paid for the map (the kill path checks
    identity on the same pass) does not pay twice.
    """
    chain: set[int] = set()
    if not pid:
        return chain

    mapping = _live_parent_map() if parent_map is None else parent_map
    current = int(pid)
    for _ in range(max(1, limit)):
        if not current or current in chain:
            break
        chain.add(current)
        current = mapping.get(current, 0)
    return chain


def is_descendant(
    candidate: int,
    ancestor: int,
    *,
    limit: int = DEFAULT_ANCESTOR_LIMIT,
    parent_map: dict[int, int] | None = None,
) -> bool:
    """Would killing ``candidate`` also take down ``ancestor``?

    This is the question ``taskkill /t`` actually asks, and it is the question
    the stop button has to ask before it does anything. Note it is true in both
    directions of the containment relation: ``is_descendant(x, x)`` is ``True``,
    because a process tree rooted at ``x`` contains ``x``.
    """
    if not candidate or not ancestor:
        return False
    return int(ancestor) in ancestors(candidate, limit=limit, parent_map=parent_map)


# --------------------------------------------------------------------------
# Live part: platform dispatch. Never raises; see the module docstring.
# --------------------------------------------------------------------------


def parent_map() -> dict[int, int]:
    """pid -> parent pid for every process visible to us.

    Empty when the platform refuses (or this is a container hiding ``/proc``),
    which callers must read as "unknown", never as "no ancestors".
    """
    try:
        if sys.platform == "win32":
            return {pid: ppid for pid, (ppid, _exe) in _windows_entries().items()}
        if sys.platform.startswith("linux"):
            return _linux_parent_map()
        return _ps_parent_map()
    except Exception:  # noqa: BLE001 - this runs inside a kill decision
        return {}


def identity(pid: int) -> ProcessIdentity | None:
    """What the OS says about ``pid``, or ``None`` if it is gone or unreadable.

    ``None`` and an all-zero ``ProcessIdentity`` are different answers and the
    kill path needs to tell them apart: "there is nothing there" means the
    process is already dead and stopping it is a no-op, while "there is
    something there but I cannot describe it" is not a licence to kill it.
    """
    if not pid:
        return None
    try:
        pid = int(pid)
        if sys.platform == "win32":
            return _windows_identity(pid)
        if sys.platform.startswith("linux"):
            return _linux_identity(pid)
        return _posix_identity(pid)
    except Exception:  # noqa: BLE001 - this runs inside a kill decision
        return None


def _live_parent_map() -> dict[int, int]:
    """Indirection so :func:`ancestors` can call the module function while
    ``parent_map`` is bound to its own parameter name."""
    return parent_map()


def alive_map(pids: Iterable[int]) -> dict[int, bool] | None:
    """Liveness for many pids in one pass — or ``None`` when this cannot answer.

    Windows has no cheap per-pid liveness. The only way to ask that does not demand
    control of somebody else's process is ``CreateToolhelp32Snapshot`` plus a walk
    of the **whole process table** (:func:`_windows_entries`). Asking it once per
    pid is what made the workflow screen expensive: a poll over ten tracked rows
    walked a 285-process table ten times, at 5.4ms a walk, to answer "nothing
    changed since last time".

    So this takes the table once and answers every pid from it with a set lookup.
    Measured on that machine it turns a 54ms poll into a 7ms one, and unlike a
    cache it cannot go stale — a process that died is simply not in the next table.

    Callers that need the exe or the creation time still use :func:`identity`: that
    extra work per pid is what this function exists to avoid.

    ``None`` means **"I could not tell"**, and that is deliberately not the same as
    "they are all dead". An unavailable API or a snapshot that came back empty
    returns ``None`` on purpose: reading that as "every process is gone" would paint
    every live tunnel red on a transient failure and make ``Parar todo`` skip them.
    On POSIX this is ``None`` too — ``os.kill(pid, 0)`` is one cheap syscall per
    pid, so there is nothing to batch, and the caller keeps its existing path.
    """
    wanted = [int(p) for p in pids if p]
    if not wanted:
        return {}
    if sys.platform != "win32":
        return None
    entries = _windows_entries()
    if not entries:
        return None
    return {pid: pid in entries for pid in wanted}


def _exe_basename(raw: str) -> str:
    """Just the program name, the way Windows reports it and the way a tracker
    entry from an older version will have stored it.

    ``os.path.basename`` splits on the native separator, which is the right one
    on each side: backslash on Windows, where a path may arrive with either
    separator, and slash on POSIX, where a backslash is a legal filename
    character and splitting on it would mangle a real name.
    """
    exe = os.path.basename((raw or "").strip())
    if sys.platform == "win32":
        exe = exe.lower()
    return exe


# --------------------------------------------------------------------------
# Windows: one toolhelp snapshot answers both "who is my parent" and "what is
# this called". GetProcessTimes answers "when did it start".
# --------------------------------------------------------------------------


_win_api = None


def _windows_api():
    """Bind kernel32 once. ``None`` means this is not Windows after all, or the
    process is sandboxed away from it; callers degrade."""
    global _win_api
    if _win_api is not None:
        return _win_api or None

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - ctypes.wintypes does not import off Windows
        _win_api = False
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            # ULONG_PTR, i.e. pointer-sized. This one field is load-bearing: a
            # struct declared with a 32-bit member on a 64-bit build shifts
            # every field after it by 8 bytes, and the API does not fail — it
            # fills the shifted fields with whatever was in memory, so the
            # parents come back as plausible-looking garbage. c_size_t is the
            # only spelling that is right on both.
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * _MAX_PATH),
        ]

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(FILETIME)
        ] * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except Exception:  # noqa: BLE001 - a stripped-down Windows, or wine
        _win_api = False
        return None

    _win_api = {
        "kernel32": kernel32,
        "entry": PROCESSENTRY32W,
        "filetime": FILETIME,
        "TH32CS_SNAPPROCESS": 0x00000002,
        "PROCESS_QUERY_LIMITED_INFORMATION": 0x1000,
        "INVALID_HANDLE_VALUE": ctypes.c_void_p(-1).value,
    }
    return _win_api


def _windows_entries() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, exe) for every process in the snapshot.

    One walk, because a snapshot is the only way Windows offers to ask this and
    asking twice means two chances to see two different machines.
    """
    api = _windows_api()
    if api is None:
        return {}

    import ctypes

    kernel32 = api["kernel32"]
    snapshot = kernel32.CreateToolhelp32Snapshot(api["TH32CS_SNAPPROCESS"], 0)
    if not snapshot or snapshot == api["INVALID_HANDLE_VALUE"]:
        return {}

    entries: dict[int, tuple[int, str]] = {}
    entry = api["entry"]()
    # The API validates this against its own compiled layout, so ask the struct
    # rather than hardcoding 568/556: on a 32-bit build the answer is different.
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            if pid:
                entries[pid] = (
                    int(entry.th32ParentProcessID),
                    _exe_basename(entry.szExeFile),
                )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return entries


def _windows_create_time(pid: int) -> float:
    """Creation time of ``pid`` in unix seconds, or ``0.0`` if we cannot open it.

    ``PROCESS_QUERY_LIMITED_INFORMATION`` rather than ``QUERY_INFORMATION``
    because the latter needs rights the user usually does not have on somebody
    else's process, and this is a best-effort check, not a security decision.
    """
    api = _windows_api()
    if api is None:
        return 0.0

    import ctypes

    kernel32 = api["kernel32"]
    handle = kernel32.OpenProcess(api["PROCESS_QUERY_LIMITED_INFORMATION"], False, pid)
    if not handle:
        return 0.0
    try:
        created = api["filetime"]()
        exited = api["filetime"]()
        kernel_time = api["filetime"]()
        user_time = api["filetime"]()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return 0.0
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 1e7 - _FILETIME_UNIX_EPOCH_DELTA
    finally:
        kernel32.CloseHandle(handle)


def _windows_identity(pid: int) -> ProcessIdentity | None:
    found = _windows_entries().get(pid)
    if found is None:
        return None
    parent_pid, exe = found
    return ProcessIdentity(
        pid=pid, parent_pid=parent_pid, exe=exe, create_time=_windows_create_time(pid)
    )


# --------------------------------------------------------------------------
# Linux: /proc is the whole answer, but stat needs parsing by hand.
# --------------------------------------------------------------------------


def parse_stat_line(raw: str) -> list[str] | None:
    """Split one ``/proc/<pid>/stat`` line into 1-based fields, or ``None``.

    Splitting the whole line on whitespace is the classic mistake here: field 2
    is the executable name in parentheses, and a process is free to be called
    ``my proc (x)``. Everything up to the **last** ``)`` is that name; the
    reliable split point is the one after it. A pid whose name contains its own
    closing paren is not a corner case — ``sh -c 'foo)'`` produces one.

    Pure and public so the parsing can be tested on a machine that has no
    ``/proc``, which is every developer running this on macOS or Windows.
    """
    close = raw.rfind(")")
    open_at = raw.find("(")
    if close < 0 or open_at < 0 or close < open_at:
        return None
    # "x" is a placeholder for field 1 (the pid), so that raw[i] below is
    # really field i+1: the slice after the paren begins at field 3.
    return ["x", raw[open_at + 1:close]] + raw[close + 1:].split()


def _proc_stat_fields(pid: int) -> list[str] | None:
    """Fields of ``/proc/<pid>/stat``, or ``None`` if there is no such process."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    return parse_stat_line(raw)


def _linux_parent_map() -> dict[int, int]:
    parents: dict[int, int] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return {}
    for name in names:
        if not name.isdigit():
            continue
        fields = _proc_stat_fields(int(name))
        if fields and len(fields) > 3:
            try:
                parents[int(name)] = int(fields[3])
            except ValueError:
                continue
    return parents


def _boot_time() -> float:
    """Seconds since the epoch at which the kernel started counting.

    ``/proc/<pid>/stat`` gives ``starttime`` in clock ticks *since boot*, which
    means nothing on its own. ``btime`` from ``/proc/stat`` is the line that
    turns it into a date.
    """
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _linux_identity(pid: int) -> ProcessIdentity | None:
    fields = _proc_stat_fields(pid)
    if not fields:
        return None
    try:
        parent_pid = int(fields[3])
        start_ticks = int(fields[21])  # field 22, 1-based
    except (IndexError, ValueError):
        return None

    create_time = 0.0
    boot = _boot_time()
    if boot:
        try:
            create_time = boot + start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, ZeroDivisionError):
            create_time = 0.0

    try:
        exe = _exe_basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        # A kernel thread, or one we may not read: /proc/<pid>/exe needs the
        # same uid on some configurations. The pid still exists, it just does
        # not tell us its name.
        exe = ""

    return ProcessIdentity(
        pid=pid, parent_pid=parent_pid, exe=exe, create_time=create_time
    )


# --------------------------------------------------------------------------
# Anything else POSIX (macOS in practice): ask ps, and say so in the docstrings
# rather than pretending.
# --------------------------------------------------------------------------


def _run_ps(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["ps", *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _ps_parent_map() -> dict[int, int]:
    """pid -> parent pid via a single ``ps -A``.

    macOS has no ``/proc``. The alternative is a syscall through ctypes, which
    means declaring ``proc_listpids``/``proc_pidinfo`` structures that are
    documented in headers rather than prose and change between releases. ``ps``
    is the answer that cannot be subtly wrong, at the cost of a fork per call —
    and this is called once per stop, not once per poll.
    """
    out = _run_ps(["-A", "-o", "ppid=", "-o", "pid="])
    if out is None:
        return {}
    parents: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[1])] = int(parts[0])
    return parents


def _posix_identity(pid: int) -> ProcessIdentity | None:
    """One pid on a platform without ``/proc``.

    ``create_time`` is reported as ``0.0`` and that is a deliberate gap, not an
    oversight: getting it would mean ``sysctl kern.proc.pid.<pid>`` per pid,
    which needs privileges this tool does not have, and the field exists to
    survive pid reuse. Callers must treat ``0.0`` as "unknown" and fall back to
    the exe comparison, which is why :data:`CREATE_TIME_TOLERANCE` never applies
    to it — a zero is not a time.
    """
    out = _run_ps(["-o", "ppid=", "-o", "comm=", "-p", str(pid)])
    if out is None:
        return None
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return None
    parts = lines[0].split(None, 1)
    try:
        parent_pid = int(parts[0])
    except (IndexError, ValueError):
        return None
    exe = _exe_basename(parts[1]) if len(parts) > 1 else ""
    return ProcessIdentity(
        pid=pid, parent_pid=parent_pid, exe=exe, create_time=0.0
    )