"""Un ambiente `local` no es un ambiente de AWS: se llega por TCP y nada más.

El riesgo que estas pruebas cubren es concreto. `Config.profile` y
`Config.region` tienen defaults con forma de AWS (`base-profile`, `us-west-2`),
y `env.local` no define ninguno de los dos, así que los hereda de `env.base`.
Como `base-profile` existe en la máquina, elegir `local` en una página de
parámetros armaba una sesión boto3 contra **AWS real** con esas credenciales.
"""

import pytest

from yappy_api.routes.envs import api_envs
from yappy_library.adapters import aws_clients
from yappy_library.application.database.sync import params as p
from yappy_library.config import Config


# --- Config.is_local -------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_client_cache():
    """El cache de clientes vive en `aws_clients` y dura todo el proceso. Sin esto,
    un cliente cacheado por un test se le sirve al siguiente y el stub deja de
    contar llamadas."""
    aws_clients.clear_cache()
    yield
    aws_clients.clear_cache()


def _cfg(tmp_path, monkeypatch, body: str):
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("", encoding="utf-8")
    (tmp_path / "env.local").write_text(body, encoding="utf-8")
    return Config("local")


def test_is_local_true_when_db_mode_is_local(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, "DB_MODE=local\n")
    assert cfg.is_local is True


@pytest.mark.parametrize("value", ["LOCAL", "  Local  ", "LoCaL\n"])
def test_is_local_tolerates_case_and_whitespace(tmp_path, monkeypatch, value):
    cfg = _cfg(tmp_path, monkeypatch, f"DB_MODE={value}\n")
    assert cfg.is_local is True


def test_is_local_false_when_db_mode_absent(tmp_path, monkeypatch):
    """Sin DB_MODE el ambiente se comporta como siempre: puede ser de AWS."""
    cfg = _cfg(tmp_path, monkeypatch, "DB_HOST=localhost\n")
    assert cfg.is_local is False


def test_is_local_false_for_another_db_mode(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, "DB_MODE=tunnel\n")
    assert cfg.is_local is False


def test_is_local_true_when_aws_environment_is_local(tmp_path, monkeypatch):
    """`env.environment.example` documenta `AWS_ENVIRONMENT=local`, no `DB_MODE`.

    Un `env.local` escrito según el template tiene que contar como local. Cuando
    no contaba, la web le calentaba clientes de AWS, le ofrecía la fila de Aurora
    y el click moría en `AWS_HOST` con un 500.
    """
    cfg = _cfg(tmp_path, monkeypatch, "AWS_ENVIRONMENT=local\n")
    assert cfg.is_local is True


@pytest.mark.parametrize("value", ["LOCAL", "  Local  ", "LoCaL\n"])
def test_aws_environment_tolerates_case_and_whitespace(tmp_path, monkeypatch, value):
    cfg = _cfg(tmp_path, monkeypatch, f"AWS_ENVIRONMENT={value}\n")
    assert cfg.is_local is True


def test_is_local_false_for_a_remote_aws_environment(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, "AWS_ENVIRONMENT=qa\n")
    assert cfg.is_local is False


def test_either_key_is_enough(tmp_path, monkeypatch):
    """No hace falta declarar las dos: una sola alcanza para ser local."""
    cfg = _cfg(tmp_path, monkeypatch, "DB_MODE=local\nAWS_ENVIRONMENT=qa\n")
    assert cfg.is_local is True


# --- La guarda de params ---------------------------------------------------


class _Cfg:
    """Config mínimo: sólo alcanza con los atributos que mira la guarda."""

    def __init__(self, local: bool):
        self.is_local = local
        self.env = "local"
        self.profile = "base-profile"
        self.region = "us-west-2"
        self.endpoint_url = None


def test_params_guard_refuses_a_local_env(monkeypatch):
    called = []
    monkeypatch.setattr(aws_clients, "_create", lambda *a, **k: called.append(a))

    with pytest.raises(p.LocalEnvHasNoAws) as exc:
        p._boto_client("ssm", _Cfg(local=True))

    assert "local" in str(exc.value).lower()


def test_params_guard_never_builds_a_session(monkeypatch):
    """El punto del guard es que no haya sesión, no sólo que avise.

    Si se construyera la sesión y después se validara, el intento de AWS ya
    habría ocurrido: el error 1064/AccessDenied del profile real llegaría antes
    de que esta guarda pudiera cortarlo.
    """
    called = []
    monkeypatch.setattr(aws_clients, "_create", lambda *a, **k: called.append(a))

    for service in ("ssm", "secretsmanager"):
        with pytest.raises(p.LocalEnvHasNoAws):
            p._boto_client(service, _Cfg(local=True))

    assert called == [], "la guarda dejó construir una sesión de boto3"


def test_params_guard_lets_a_normal_env_through(monkeypatch):
    """Un ambiente que no es local sigue llegando a boto3 como siempre."""
    calls = []
    monkeypatch.setattr(
        aws_clients,
        "_create",
        lambda service, profile, region, endpoint: calls.append(
            (service, profile, region, endpoint)
        )
        or object(),
    )

    p._boto_client("ssm", _Cfg(local=False))
    assert calls == [("ssm", "base-profile", "us-west-2", None)]


# --- /api/envs -------------------------------------------------------------


def test_api_envs_reports_is_local(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_PROFILE=base-profile\n", encoding="utf-8")
    (tmp_path / "env.dev").write_text("AWS_PROFILE=localstack\n", encoding="utf-8")
    (tmp_path / "env.local").write_text("DB_MODE=local\n", encoding="utf-8")

    payload = api_envs()
    by_env = {e["env"]: e for e in payload["environments"]}

    assert by_env["local"]["is_local"] is True
    assert by_env["dev"]["is_local"] is False


def test_api_envs_keeps_a_broken_env_visible(tmp_path, monkeypatch):
    """Un ambiente que no carga sigue en la lista, con su load_error.

    Regresión: si `is_local` se leyera fuera del try, un ambiente roto
    rompería el endpoint entero y la UI perdería todos los ambientes.
    """
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_PROFILE=base-profile\n", encoding="utf-8")
    (tmp_path / "env.local").write_text("DB_MODE=local\n", encoding="utf-8")

    real_with_env = Config.with_env
    monkeypatch.setattr(
        Config,
        "with_env",
        classmethod(lambda cls, env: (_ for _ in ()).throw(ValueError("roto"))
                    if env == "local"
                    else real_with_env(env)),
    )

    payload = api_envs()
    by_env = {e["env"]: e for e in payload["environments"]}

    assert "local" in by_env
    assert by_env["local"]["load_error"] == "roto"
    assert by_env["local"]["is_local"] is False
