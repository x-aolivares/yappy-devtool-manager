"""Port selection and preflight binding for `yappy web`.

Windows is the platform that makes these matter: a port reserved by
Hyper-V/WSL2/Docker fails with 10013 WSAEACCES, which reads like a permissions
problem and sends people looking at the wrong thing.
"""
import os
import socket
import sys

import pytest

from web.api.ports_registry import (
    ENV_WEB_API_PORT,
    ENV_WEB_UI_PORT,
    YappyPort,
    web_api_port,
    web_ui_port,
)


def test_defaults_come_from_the_enum():
    assert web_api_port() == int(YappyPort.WEB_API)
    assert web_ui_port() == int(YappyPort.WEB_UI_DEV)


def test_env_override_moves_the_port(monkeypatch):
    monkeypatch.setenv(ENV_WEB_API_PORT, "8399")
    assert web_api_port() == 8399
    # The UI is independent: overriding one must not drag the other.
    assert web_ui_port() == int(YappyPort.WEB_UI_DEV)


def test_blank_override_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV_WEB_API_PORT, "   ")
    assert web_api_port() == int(YappyPort.WEB_API)


@pytest.mark.parametrize("bad", ["abc", "80", "70000", "-1"])
def test_unusable_override_raises_instead_of_silently_using_the_default(monkeypatch, bad):
    """Falling back would bind the default and leave the user wondering why
    their chosen port was ignored."""
    monkeypatch.setenv(ENV_WEB_API_PORT, bad)
    with pytest.raises(ValueError):
        web_api_port()


def test_the_api_command_uses_the_port_it_is_given():
    """The port is a parameter, not re-read from the env: that is what keeps the
    port uvicorn binds and the port the UI proxy points at the same one."""
    from cli.verbs.web import _api_command

    cmd = _api_command(8399, reload=False)
    assert cmd[cmd.index("--port") + 1] == "8399"
    assert "--reload" not in cmd


def test_the_generated_proxy_follows_the_resolved_api_port(monkeypatch, tmp_path):
    """The proxy is generated, not committed. If it pointed at one port while
    uvicorn bound another, the UI would 404 with no explanation."""
    from cli.verbs import web as web_verb

    monkeypatch.setattr(web_verb, "_FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(web_verb, "_node_executable", lambda: "node")
    written = {}

    class FakeBin:
        exists = staticmethod(lambda: True)
        __str__ = staticmethod(lambda: "/tmp/ng.js")

    monkeypatch.setattr(web_verb, "_NG_BIN", FakeBin())
    monkeypatch.setattr(
        "web.api.proxy_config.write",
        lambda frontend, port: written.setdefault("port", port) or tmp_path / "p.json",
    )

    web_verb._ui_command(8399, 4300)
    assert written["port"] == 8399


# --- dynamic port selection -----------------------------------------------
#
# `yappy web` takes a free port instead of dying. The classic collision is the
# child of a dead `uvicorn --reload`, which keeps the listening socket after its
# parent is gone — being unable to start because of a process you cannot see is
# worse than starting on another port. DB ports are the opposite case (pinned in
# `config/env.*` for DBeaver), so this leniency is confined to the web verb.


def _hold_port() -> tuple[socket.socket, int]:
    """A socket LISTENING on some free port, to make that port busy."""
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    return held, held.getsockname()[1]


def test_a_free_port_is_used_as_is():
    from cli.verbs import web as web_verb

    free = web_verb._find_free_port(int(YappyPort.WEB_API))
    assert free is not None
    assert web_verb._resolve_port(free, "the API", ENV_WEB_API_PORT) == free


def test_a_busy_port_is_stepped_over_instead_of_failing():
    from cli.verbs import web as web_verb

    held, port = _hold_port()
    try:
        chosen = web_verb._resolve_port(port, "the API", ENV_WEB_API_PORT)
    finally:
        held.close()

    assert chosen != port
    assert web_verb._port_is_free(chosen), "the chosen port has to be bindable"


def test_the_move_is_announced_with_who_held_the_port(monkeypatch):
    """Silently starting elsewhere would leave the user staring at a port they
    did not ask for, or missing that an old instance of theirs is still up."""
    from cli.verbs import web as web_verb

    held, port = _hold_port()
    said: list[str] = []
    monkeypatch.setattr(web_verb, "warn", lambda msg: said.append(str(msg)))
    monkeypatch.setattr(web_verb, "_describe_owner", lambda p: (22852, "python.exe", True))
    try:
        chosen = web_verb._resolve_port(port, "the API", ENV_WEB_API_PORT)
    finally:
        held.close()

    joined = " ".join(said)
    assert str(port) in joined and str(chosen) in joined
    assert "python.exe" in joined and "22852" in joined
    assert "yappy stop web" in joined


def test_the_override_is_a_starting_point_not_a_dead_end(monkeypatch):
    """`YAPPY_WEB_API_PORT` says where to look. Pinning it is the escape hatch
    from a busy default, so failing there would defeat its own purpose."""
    from cli.verbs import web as web_verb

    busy = OSError("address already in use")
    busy.errno = 10048
    monkeypatch.setattr(web_verb, "_try_bind", lambda port: busy if port == 8399 else None)
    monkeypatch.setattr(web_verb, "warn", lambda msg: None)

    assert web_verb._resolve_port(8399, "the API", ENV_WEB_API_PORT) == 8400


def test_the_scan_finds_the_first_free_port(monkeypatch):
    from cli.verbs import web as web_verb

    monkeypatch.setattr(web_verb, "_port_is_free", lambda port: port == 8302)
    assert web_verb._find_free_port(8301) == 8302


def test_the_scan_is_bounded(monkeypatch):
    """If everything nearby is taken it has to give up, not scan to 65535."""
    from cli.verbs import web as web_verb

    monkeypatch.setattr(web_verb, "_port_is_free", lambda port: False)
    assert web_verb._find_free_port(8301) is None


def test_nothing_free_in_range_dies_with_the_real_reason(monkeypatch):
    from cli.verbs import web as web_verb

    err = OSError("address already in use")
    err.errno = 10048
    monkeypatch.setattr(web_verb, "_try_bind", lambda port: err)
    monkeypatch.setattr(web_verb, "_port_is_free", lambda port: False)
    monkeypatch.setattr(web_verb, "_describe_owner", lambda port: None)

    with pytest.raises(SystemExit):
        web_verb._resolve_port(8300, "the API", ENV_WEB_API_PORT)


def test_windows_10013_is_explained_as_a_reserved_range(monkeypatch):
    """The number is the whole point: 10013 looks like permissions, and the
    message has to say it's the port reservation instead."""
    from cli.verbs import web as web_verb

    monkeypatch.setattr(web_verb.sys, "platform", "win32")
    err = OSError("permission denied")
    err.winerror = 10013

    msg = web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)
    assert "excludedportrange" in msg
    assert ENV_WEB_API_PORT in msg


def test_address_in_use_is_told_to_stop_the_old_process(monkeypatch):
    from cli.verbs import web as web_verb

    err = OSError("address already in use")
    err.winerror = 10048
    msg = web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)
    assert "yappy stop web" in msg
    assert "netstat" in msg


def test_every_message_offers_the_escape_hatch(monkeypatch):
    from cli.verbs import web as web_verb

    for code in (10013, 10048, None):
        err = OSError("boom")
        err.errno = code
        assert ENV_WEB_API_PORT in web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)


# --- identifying the process holding the port -----------------------------

# Real `netstat -ano -p TCP` output, trimmed. The columns are the whole reason
# this is parsed rather than grepped.
NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1044
  TCP    127.0.0.1:8300         0.0.0.0:0              LISTENING       22852
  TCP    127.0.0.1:8300         127.0.0.1:55001        ESTABLISHED     22852
  TCP    127.0.0.1:54321        127.0.0.1:8300         ESTABLISHED     9931
  TCP    127.0.0.1:4300         0.0.0.0:0              LISTENING       7712
  TCP    [::]:445               [::]:0                 LISTENING       4
"""


def test_finds_the_listening_pid_not_the_connected_one():
    """Port 8300 appears twice. Only the LISTENING row owns the port; picking
    the ESTABLISHED one would blame whatever is merely talking to the server."""
    from cli.verbs.web import _parse_netstat_listeners

    assert _parse_netstat_listeners(NETSTAT, 8300) == 22852


def test_ipv6_listener_is_matched_too():
    from cli.verbs.web import _parse_netstat_listeners

    assert _parse_netstat_listeners(NETSTAT, 445) == 4


def test_port_suffix_does_not_match_a_longer_one():
    """"8300" must not match "83001"."""
    from cli.verbs.web import _parse_netstat_listeners

    assert _parse_netstat_listeners(
        "  TCP    127.0.0.1:83001        0.0.0.0:0              LISTENING       7", 8300
    ) is None


def test_no_listener_gives_none():
    from cli.verbs.web import _parse_netstat_listeners

    assert _parse_netstat_listeners(NETSTAT, 9999) is None
    assert _parse_netstat_listeners("", 8300) is None


def test_a_leftover_of_ours_is_named_and_gets_the_clean_command(monkeypatch):
    from cli.verbs import web as web_verb

    err = OSError("address already in use")
    err.errno = 10048
    monkeypatch.setattr(web_verb, "_describe_owner", lambda port: (22852, "python.exe", True))

    msg = web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)
    assert "python.exe" in msg and "22852" in msg
    assert "yappy stop web" in msg


def test_a_foreign_process_is_not_silently_killed_by_our_advice(monkeypatch):
    """If it isn't a tracked yappy process, 'yappy stop web' would not touch it,
    so pointing there first would waste the user's time."""
    from cli.verbs import web as web_verb

    err = OSError("address already in use")
    err.errno = 10048
    monkeypatch.setattr(web_verb, "_describe_owner", lambda port: (9931, "Docker.exe", False))

    msg = web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)
    assert "Docker.exe" in msg and "9931" in msg
    assert "taskkill /PID 9931 /T /F" in msg


def test_unidentified_owner_falls_back_to_the_generic_help(monkeypatch):
    from cli.verbs import web as web_verb

    err = OSError("address already in use")
    err.errno = 10048
    monkeypatch.setattr(web_verb, "_describe_owner", lambda port: None)

    msg = web_verb._port_error(8300, "the API", ENV_WEB_API_PORT, err)
    assert "netstat" in msg


# --- liveness probe -------------------------------------------------------


def test_liveness_probe_does_not_use_os_kill_on_windows(monkeypatch):
    """The regression this guards: os.kill(pid, 0) is not a probe on Windows,
    it is TerminateProcess. Asking 'are you alive?' must never kill the answer."""
    from library import process_tracker

    monkeypatch.setattr(sys, "platform", "win32")
    called = []
    monkeypatch.setattr(process_tracker.os, "kill", lambda *a: called.append(a))

    process_tracker._pid_alive(4242)

    assert called == [], f"os.kill must not be used on Windows; it kills the target: {called}"


def test_liveness_probe_still_uses_os_kill_on_posix(monkeypatch):
    from library import process_tracker

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(process_tracker.os, "kill", lambda pid, sig: None)
    assert process_tracker._pid_alive(os.getpid()) is True


def test_liveness_probe_handles_permission_errors(monkeypatch):
    """EPERM means the process exists but is not ours — alive. Treating it as
    dead would make `yappy status` hide other users' running processes."""
    from library import process_tracker

    monkeypatch.setattr(sys, "platform", "linux")

    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(process_tracker.os, "kill", denied)
    assert process_tracker._pid_alive(1) is True


def test_liveness_probe_reports_gone(monkeypatch):
    from library import process_tracker

    monkeypatch.setattr(sys, "platform", "linux")

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(process_tracker.os, "kill", gone)
    assert process_tracker._pid_alive(999999) is False
