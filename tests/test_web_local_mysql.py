"""Tests del servicio de MySQL local.

No hay MySQL en este entorno, asi que el arranque real no se ejercita: lo que se
verifica es la logica que lo rodea y que decide el contrato con el usuario
(comando configurable, probe antes de afirmar exito, errores accionables).
"""
import subprocess

import pytest

from web.api.domain.exceptions import (
    ConfigKeyMissingError,
    LocalMysqlUnavailableError,
)
from web.api.infrastructure import local_mysql_service as svc_mod
from web.api.infrastructure.local_mysql_service import LocalMysqlService


class FakeConfig:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    @classmethod
    def known_environments(cls):
        return []


# --- status ---------------------------------------------------------------


@pytest.mark.parametrize(
    "running,detail_present", [(True, False), (False, True)]
)
def test_status_reflects_the_tcp_probe(monkeypatch, running, detail_present):
    monkeypatch.setattr(
        svc_mod, "tcp_probe", lambda *a, **kw: (running, "" if running else "refused")
    )
    cfg = FakeConfig(
        {"LOCAL_DB_HOST": "127.0.0.1", "LOCAL_DB_PORT": "3306", "LOCAL_DB_USER": "root"}
    )

    status = LocalMysqlService(cfg).status()

    assert status.running is running
    assert bool(status.detail) is detail_present
    assert (status.host, status.port) == ("127.0.0.1", 3306)


def test_status_exposes_the_configured_start_command(monkeypatch):
    """The UI needs to show the user the exact command to run themselves."""
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))
    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "net start MySQL80"})

    assert LocalMysqlService(cfg).status().start_command == "net start MySQL80"


def test_status_never_raises_on_a_broken_config(monkeypatch):
    """Status is polled constantly by the UI; it must degrade, not explode."""
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))
    cfg = FakeConfig({"LOCAL_DB_USER": ""})  # empty -> raises inside the spec

    status = LocalMysqlService(cfg).status()

    assert status.running is False
    assert status.user == "?", "an unusable user must not break the status call"


# --- start ----------------------------------------------------------------


def test_start_is_a_noop_when_already_running(monkeypatch):
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (True, ""))
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: ran.append(a) or None)

    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "net start MySQL80"})
    status = LocalMysqlService(cfg).start()

    assert status.running is True
    assert ran == [], "must not re-run the start command on a running server"


def test_start_without_a_configured_command_names_the_key(monkeypatch):
    """No command is invented. Guessing a Windows service name would produce a
    confusing failure, so the user is told exactly which key to set."""
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))
    cfg = FakeConfig({})

    with pytest.raises(ConfigKeyMissingError) as info:
        LocalMysqlService(cfg).start()

    assert "LOCAL_MYSQL_START_CMD" in str(info.value)


def test_start_reports_a_missing_binary(monkeypatch):
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))

    def boom(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(subprocess, "run", boom)
    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "C:/xampp/mysql/bin/mysqld.exe"})

    with pytest.raises(LocalMysqlUnavailableError) as info:
        LocalMysqlService(cfg).start()

    assert "mysqld.exe" in str(info.value)
    assert "net start" in str(info.value), "should suggest the service form too"


def test_start_reports_a_non_zero_exit(monkeypatch):
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))

    class Completed:
        returncode = 2
        stdout = ""
        stderr = "The service name is invalid."

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Completed())
    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "net start MySQL80"})

    with pytest.raises(LocalMysqlUnavailableError) as info:
        LocalMysqlService(cfg).start()

    assert "service name is invalid" in str(info.value)


def test_start_fails_when_the_command_succeeds_but_nothing_listens(monkeypatch):
    """`net start` can exit 0 and still leave the server down. Reporting success
    there would send the user off to debug the wrong thing."""
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))
    monkeypatch.setattr(svc_mod, "time", _FakeClock())

    class Completed:
        returncode = 0
        stdout = "started"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Completed())
    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "net start MySQL80"})

    with pytest.raises(LocalMysqlUnavailableError) as info:
        LocalMysqlService(cfg).start()

    assert "never accepted connections" in str(info.value)
    assert "LOCAL_DB_PORT" in str(info.value), "should point at the port config"


def test_start_succeeds_once_the_port_opens(monkeypatch):
    """The realistic case: the server takes a couple of seconds to bind."""
    attempts = {"n": 0}

    def probe(*a, **kw):
        attempts["n"] += 1
        return (attempts["n"] >= 3, "")

    monkeypatch.setattr(svc_mod, "tcp_probe", probe)
    monkeypatch.setattr(svc_mod, "time", _FakeClock())

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Completed())
    cfg = FakeConfig({"LOCAL_MYSQL_START_CMD": "net start MySQL80"})

    assert LocalMysqlService(cfg).start().running is True
    assert attempts["n"] >= 3, "must actually wait for the port"


# --- stop -----------------------------------------------------------------


def test_stop_without_a_configured_command_names_the_key(monkeypatch):
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (True, ""))

    with pytest.raises(ConfigKeyMissingError) as info:
        LocalMysqlService(FakeConfig({})).stop()

    assert "LOCAL_MYSQL_STOP_CMD" in str(info.value)


def test_stop_waits_for_the_port_to_close(monkeypatch):
    states = [(True, ""), (True, ""), (False, "")]

    def probe(*a, **kw):
        return states.pop(0) if states else (False, "")

    monkeypatch.setattr(svc_mod, "tcp_probe", probe)
    monkeypatch.setattr(svc_mod, "time", _FakeClock())

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Completed())
    cfg = FakeConfig({"LOCAL_MYSQL_STOP_CMD": "net stop MySQL80"})

    assert LocalMysqlService(cfg).stop().running is False


# --- command splitting ----------------------------------------------------


def test_windows_paths_are_not_mangled_by_shlex(monkeypatch):
    """posix shlex eats backslashes, so a Windows path has to be split with
    posix=False or `C:\\xampp\\mysql` becomes `C:xamppmysql`."""
    monkeypatch.setattr(svc_mod.sys, "platform", "win32")

    argv = svc_mod._split_command(r"C:\xampp\mysql\bin\mysqld.exe --console")

    assert argv[0] == r"C:\xampp\mysql\bin\mysqld.exe"
    assert "--console" in argv


def test_posix_paths_split_normally(monkeypatch):
    monkeypatch.setattr(svc_mod.sys, "platform", "linux")

    argv = svc_mod._split_command("/usr/sbin/mysqld --user=mysql")

    assert argv == ["/usr/sbin/mysqld", "--user=mysql"]


# --- verify_login ---------------------------------------------------------


def test_verify_login_reports_a_bad_password(monkeypatch):
    """Listening is not the same as usable: the credentials are what the
    migration will actually use."""
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (True, ""))

    def boom(spec, **kw):
        raise LocalMysqlUnavailableError("Access denied for user 'root'")

    monkeypatch.setattr(svc_mod, "connect", boom)
    cfg = FakeConfig({"LOCAL_DB_USER": "root", "LOCAL_DB_PASSWORD": "wrong"})

    with pytest.raises(LocalMysqlUnavailableError) as info:
        LocalMysqlService(cfg).verify_login()

    assert "LOCAL_DB_USER" in str(info.value)
    assert "LOCAL_DB_PASSWORD" in str(info.value)


def test_verify_login_reports_a_stopped_server(monkeypatch):
    monkeypatch.setattr(svc_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))
    cfg = FakeConfig({})

    with pytest.raises(LocalMysqlUnavailableError) as info:
        LocalMysqlService(cfg).verify_login()

    assert "not listening" in str(info.value)


class _FakeClock:
    """Deterministic stand-in for the `time` module inside the service.

    Each `monotonic()` advances 0.4s, past the 0.5s poll interval, so a loop of
    N iterations finishes in microseconds instead of N * 0.5s.
    """

    def __init__(self):
        self._t = 0.0

    def monotonic(self):
        self._t += 0.4
        return self._t

    def sleep(self, _seconds):
        return None
