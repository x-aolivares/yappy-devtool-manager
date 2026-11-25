"""El logout de sesión AWS se puede pedir desde la web.

El device flow no extiende la sesión de Identity Center: para que el próximo
login arranque una sesión nueva hay que limpiar el cache local. El endpoint
usa la CLI configurada y, si ésta falla o no está, borra igual los caches de
token para que el botón no mienta.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yappy_api.routes import aws as routes
from yappy_library.adapters import aws_sso


def _prepare(tmp_path: Path) -> Path:
    (tmp_path / "config").write_text(
        "[profile base-profile]\n"
        "sso_session = sso-base\n"
        "\n"
        "[sso-session sso-base]\n"
        "sso_start_url = http://example.awsapps.com/start\n"
        "sso_region = us-east-1\n",
        encoding="utf-8",
    )
    token = aws_sso.sso_cache_path("sso-base")
    token.write_text(
        json.dumps({"accessToken": "token", "expiresAt": "2099-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    registration = token.parent / "registration.json"
    registration.write_text(json.dumps({"clientId": "client"}), encoding="utf-8")
    return token


@pytest.fixture
def _entorno(tmp_path, monkeypatch):
    # Primero se aísla `aws_dir`: si se prepara el cache antes, la ruta
    # `sso_cache_path` todavía apunta al ~/.aws real y el test lo ensucia.
    monkeypatch.setattr(routes, "aws_dir", lambda: tmp_path)
    monkeypatch.setattr(aws_sso, "aws_dir", lambda: tmp_path)
    monkeypatch.setattr(routes, "_default_profile", lambda: "base-profile")
    monkeypatch.setattr(routes, "aws_cli_command", lambda cfg: ["aws"])
    return _prepare(tmp_path)


def test_logout_usa_la_cli_y_deja_la_sesion_inactiva(_entorno, monkeypatch):
    commands: list[list[str]] = []

    def _run(cmd, **kwargs):
        commands.append(cmd)
        # La CLI real borra los caches; acá lo hace el fallback del endpoint.
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(routes.subprocess, "run", _run)

    result = routes.aws_session_logout()

    assert result["ok"] is True
    assert commands == [["aws", "sso", "logout"]]
    assert not _entorno.exists()
    # El cache de registro no es una sesión: se conserva.
    assert (_entorno.parent / "registration.json").exists()
    assert routes._status_for_profile("base-profile")["active"] is False


def test_si_la_cli_no_esta_igual_cierra_la_sesion(_entorno, monkeypatch):
    def _sin_cli(cmd, **kwargs):
        raise FileNotFoundError("aws")

    monkeypatch.setattr(routes.subprocess, "run", _sin_cli)

    result = routes.aws_session_logout()

    assert result["ok"] is True
    assert "CLI avisó" in result["message"]
    assert not _entorno.exists()
