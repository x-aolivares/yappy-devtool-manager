"""`Session.start()` abre un login SSO cuando no hay sesión viva.

Antes de este cambio la línea era ``from ..aws.session import session``, que
desde ``yappy_library/application`` resuelve a ``yappy_library.aws``: un
paquete que no existe. El import fallaba con ``ModuleNotFoundError``, así que
el camino que se toma justamente cuando falta la sesión era el único que no
funcionaba — y el error era un ``ModuleNotFoundError`` de un import, no el
"no hay sesión" que el developer esperaba ver.

El login vive ahora en ``yappy_library.adapters.aws_sso`` porque la library no
puede importar el CLI, y lo necesitan los dos: ``yappy login aws`` y
``Session.start()``.
"""

from __future__ import annotations

import types

from yappy_library.application import session as session_mod
from yappy_library.config import Config


class _FakeCmd:
    """Un `BaseCommand` que sólo sabe responder `get-caller-identity`."""

    def __init__(self, returncodes: list[int]):
        self._returncodes = list(returncodes)
        self.calls: list[list[str]] = []

    def validate_env(self, env: str) -> None:
        pass

    def check_requirements(self, tool: str) -> None:
        pass

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        code = self._returncodes.pop(0) if self._returncodes else 0
        return types.SimpleNamespace(returncode=code, stdout="", stderr="")


def _session(monkeypatch, returncodes: list[int]):
    monkeypatch.setattr(
        Config,
        "with_env",
        staticmethod(lambda env: types.SimpleNamespace(profile="base-profile", env=env)),
    )
    sess = session_mod.Session("dev")
    sess._cmd = _FakeCmd(returncodes)
    return sess


def test_start_opens_an_sso_login_when_there_is_no_live_session(monkeypatch):
    sess = _session(monkeypatch, [1, 0])
    logins: list[str] = []
    monkeypatch.setattr(session_mod, "sso_login", lambda profile: logins.append(profile))

    sess.start()

    assert logins == ["base-profile"]


def test_start_checks_the_session_again_after_the_login(monkeypatch):
    """Si el login no sirvió, `start` tiene que fallar en vez de seguir como si nada."""
    sess = _session(monkeypatch, [1, 1])
    monkeypatch.setattr(session_mod, "sso_login", lambda profile: None)

    try:
        sess.start()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("start() debió fallar: la sesión sigue sin credenciales")


def test_start_does_not_log_in_when_the_session_is_already_alive(monkeypatch):
    sess = _session(monkeypatch, [0])
    logins: list[str] = []
    monkeypatch.setattr(session_mod, "sso_login", lambda profile: logins.append(profile))

    sess.start()

    assert logins == []


def test_the_library_does_not_import_a_module_that_does_not_exist(monkeypatch):
    """El bug era un import relativo mal apuntado: `..aws` desde `application/`.

    Se comprueba que el módulo de la library importe el login por su nombre
    absoluto, que es lo que lo hace immune a dónde quede la carpeta que lo
    contiene.
    """
    assert session_mod.sso_login.__module__ == "yappy_library.adapters.aws_sso"
    assert session_mod.sso_login.__name__ == "login"
