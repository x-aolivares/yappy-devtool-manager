"""Port selection and preflight binding for `yappy web`.

Windows is the platform that makes these matter: a port reserved by
Hyper-V/WSL2/Docker fails with 10013 WSAEACCES, which reads like a permissions
problem and sends people looking at the wrong thing.
"""
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
