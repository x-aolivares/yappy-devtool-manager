"""``BASE_AWS_CLI`` elige qué AWS CLI usa la app.

Antes el token de RDS se generaba con ``python -m awscli`` y los túneles
probaban primero el binario ``aws`` sin que la config dijera nada. Ahora el
base profile decide: ``native`` (default) usa la AWS CLI instalada y
``emulator`` conserva el camino de Python para quien dependa de él.

Lo que se pintea acá es que la elección llega *de verdad* al comando que se
ejecuta —no sólo a una constante—, incluido el token de RDS, que era el único
lugar con ``python -m awscli`` escrito en el código.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from yappy_library.adapters import processes
from yappy_library.adapters.database import credentials
from yappy_library.adapters.aws_errors import AwsSessionExpiredError
from yappy_library.config import Config, aws_cli_mode


def _cfg(mode: str | None = None) -> Config:
    values = {
        "AWS_PROFILE": "base-profile",
        "AWS_HOST": "db.example.com",
        "AWS_REGION": "us-west-2",
        "AWS_PORT": "53360",
        "DB_USER": "app",
    }
    if mode is not None:
        values["BASE_AWS_CLI"] = mode
    return Config._from_values(values)


@pytest.fixture(autouse=True)
def _clear_aws_cmd_cache():
    processes._aws_cmd.cache_clear()
    yield
    processes._aws_cmd.cache_clear()


# --- la resolución de la config -----------------------------------------------


def test_sin_la_clave_usa_la_cli_nativa():
    assert aws_cli_mode(_cfg()) == "native"
    assert processes.aws_cli_command(_cfg()) == ["aws"]


def test_emulator_usa_el_awscli_de_python():
    assert processes.aws_cli_command(_cfg("emulator")) == [
        sys.executable, "-m", "awscli",
    ]


def test_el_valor_se_normaliza():
    assert aws_cli_mode(_cfg("  NATIVE  ")) == "native"
    assert aws_cli_mode(_cfg("Emulator")) == "emulator"


def test_un_valor_desconocido_avisa_y_cae_a_nativo(monkeypatch):
    avisos: list[str] = []
    monkeypatch.setattr(processes, "warn", avisos.append)

    assert processes.aws_cli_command(_cfg("otro")) == ["aws"]
    assert any("BASE_AWS_CLI" in aviso for aviso in avisos)


def test_aws_cmd_respeta_el_modo_aunque_ambas_clis_funcionen(monkeypatch):
    monkeypatch.setattr(
        processes.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    assert processes._aws_cmd("emulator") == [sys.executable, "-m", "awscli"]
    assert processes._aws_cmd("native") == ["aws"]


def test_aws_cmd_usa_el_awscli_de_python_si_el_base_profile_lo_pide(monkeypatch):
    """Aunque el nativo esté instalado, el modo emulator gana."""
    monkeypatch.setattr(
        processes.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    assert processes._aws_cmd("emulator") == [sys.executable, "-m", "awscli"]


def test_el_cache_es_por_modo_no_por_instancia_de_config(monkeypatch):
    """La web crea un Config por request: cachear por objeto crecería sin límite."""
    monkeypatch.setattr(
        processes.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    processes._aws_cmd.cache_clear()

    for _ in range(3):
        processes._aws_cmd(processes._resolved_aws_cli_mode(_cfg("native")))

    assert processes._aws_cmd.cache_info().currsize == 1


# --- el token de RDS -----------------------------------------------------------


def _token_desde_cli(monkeypatch, modo: str | None, *, cmd_publicado: list[str]):
    cfg = _cfg(modo)
    monkeypatch.setattr(credentials, "_aws_cmd", lambda cfg: cmd_publicado.copy())
    monkeypatch.setattr(
        credentials.subprocess, "run",
        lambda cmd, **kw: (
            captured.append(cmd)
            or SimpleNamespace(returncode=0, stdout="token-de-rds\n", stderr="")
        ),
    )
    # Si se llegara a botocore, el test tiene que fallar: la CLI configurada
    # es la que debe generar el token.
    def _sin_botocore(*args, **kwargs):
        raise AssertionError("se usó botocore en vez de la CLI configurada")

    monkeypatch.setattr(credentials.botocore.session, "Session", _sin_botocore)
    captured: list[list[str]] = []
    return cfg, captured


def test_el_token_de_rds_usa_la_cli_native(monkeypatch):
    cfg, captured = _token_desde_cli(monkeypatch, None, cmd_publicado=["aws"])

    assert credentials.generate_token(cfg) == "token-de-rds"
    cmd = captured[0]
    assert cmd[:1] == ["aws"]
    assert "-m" not in cmd and "awscli" not in cmd
    assert cmd[1:3] == ["rds", "generate-db-auth-token"]
    assert cmd[cmd.index("--hostname") + 1] == "db.example.com"
    assert cmd[cmd.index("--username") + 1] == "app"
    assert cmd[cmd.index("--profile") + 1] == "base-profile"


def test_el_token_de_rds_usa_el_modulo_en_modo_emulator(monkeypatch):
    cfg, captured = _token_desde_cli(
        monkeypatch, "emulator", cmd_publicado=[sys.executable, "-m", "awscli"],
    )

    assert credentials.generate_token(cfg) == "token-de-rds"
    assert captured[0][:3] == [sys.executable, "-m", "awscli"]


def test_una_sesion_vencida_en_la_cli_no_intenta_botocore(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(credentials, "_aws_cmd", lambda cfg: ["aws"])
    monkeypatch.setattr(
        credentials.subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(
            returncode=255,
            stdout="",
            stderr="Error when retrieving token from sso: Token has expired and refresh failed",
        ),
    )

    def _sin_botocore(*args, **kwargs):
        raise AssertionError("una sesión vencida no debe caer a botocore")

    monkeypatch.setattr(credentials.botocore.session, "Session", _sin_botocore)

    with pytest.raises(AwsSessionExpiredError):
        credentials.generate_token(cfg)
