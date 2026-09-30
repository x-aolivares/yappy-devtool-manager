"""Port selection and preflight binding for `yappy web`.

Windows is the platform that makes these matter: a port reserved by
Hyper-V/WSL2/Docker fails with 10013 WSAEACCES, which reads like a permissions
problem and sends people looking at the wrong thing.
"""
import os
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


def test_the_api_cli_command_uses_the_resolved_port(monkeypatch):
    from cli.verbs.web import _api_command

    monkeypatch.setenv(ENV_WEB_API_PORT, "8399")
    cmd = _api_command(reload=False)
    assert cmd[cmd.index("--port") + 1] == "8399"


def test_the_generated_proxy_follows_the_api_override(monkeypatch, tmp_path):
    """The proxy is generated, not committed. If it used the enum while uvicorn
    used the override, the UI would 404 with no explanation."""
    from cli.verbs import web as web_verb

    monkeypatch.setenv(ENV_WEB_API_PORT, "8399")
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

    web_verb._ui_command()
    assert written["port"] == 8399


# --- preflight ------------------------------------------------------------


def test_free_port_passes(monkeypatch):
    from cli.verbs.web import check_port_free

    check_port_free(0, "the API", ENV_WEB_API_PORT)  # port 0 = ask the OS


def test_busy_port_dies_with_the_command_to_find_the_owner(monkeypatch):
    import socket as socket_mod

    from cli.verbs import web as web_verb

    held = socket_mod.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        with pytest.raises(SystemExit):
            web_verb.check_port_free(port, "the API", ENV_WEB_API_PORT)
    finally:
        held.close()


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
