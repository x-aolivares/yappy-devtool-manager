"""La fila de un ambiente no se pierde cuando una etapa falla.

El punto de este archivo es que la cadena tiene cuatro saltos y cada uno puede
fallar solo. Lo que ya se resolvió —familia, revisión, pipeline— tiene que
seguir visible cuando el salto que viene después no se pudo dar: si falta el
token de CircleCI, la fila tiene que decir "pipeline 210, rama desconocida", no
volverse una celda en blanco. Una tabla con huecos no se puede comparar.
"""

from __future__ import annotations

import pytest

from yappy_api.routes import deployments
from yappy_library.application.deployment import ecs_branch
from yappy_library.application.deployment.ecs_branch import DeploymentUnresolved
from yappy_library.config import Config

PARAMETERS = {
    "group_name": "trnxd",
    "service_name": "payment-aggregator",
    "cluster": "capabilities",
}


class _Cfg:
    def __init__(self, **values):
        self.env = values.pop("env", "qa")
        self.profile = values.pop("profile", "base-profile")
        self.region = values.pop("region", "us-west-1")
        self.is_local = values.pop("is_local", False)
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture(autouse=True)
def sin_circleci(monkeypatch):
    """El ECS resuelto y sin token de CircleCI: el corte es siempre la etapa 4.

    Autouse porque todos los tests de la fila necesitan lo mismo para llegar a
    CircleCI, y cada uno repitiendo el monkeypatch duplica el comportamiento
    en dos lugares. Además `_Cfg` no trae CIRCLECI_TOKEN, que es justo lo que
    hace que la última etapa falle con el status que se está probando.
    """
    monkeypatch.setattr(
        ecs_branch,
        "deployed_task_definition",
        lambda cfg, family, service: (39, "ecr/payment-aggregator:build.210"),
    )
    monkeypatch.setattr(Config, "with_env", classmethod(lambda cls, env: _Cfg(env=env)))


def test_sin_token_la_fila_conserva_lo_que_ya_se_resolvio():
    """El motivo del corte es el token, pero familia, revisión y pipeline quedan."""

    row = ecs_branch.environment_deployment("repo", "qa", PARAMETERS)

    assert row["status"] == "circleci_unavailable"
    assert row["task_definition_family"] == "yappy-trnxd-payment-aggregator-qa-family"
    assert row["task_definition_revision"] == 39
    assert row["pipeline_id"] == 210
    assert row["branch"] is None
    assert "CIRCLECI_TOKEN" in row["message"]


def test_una_familia_inexistente_no_inventa_rama(monkeypatch):
    """Sin task definition no hay ni pipeline: la fila dice sólo eso."""

    def _raise(cfg, family, service):
        raise DeploymentUnresolved(
            "no_task_definition", f"ECS no tiene la familia '{family}' en {cfg.region}."
        )

    monkeypatch.setattr(ecs_branch, "deployed_task_definition", _raise)
    monkeypatch.setattr(Config, "with_env", classmethod(lambda cls, env: _Cfg(env=env)))

    row = ecs_branch.environment_deployment("repo", "qa", PARAMETERS)

    assert row["status"] == "no_task_definition"
    assert row["pipeline_id"] is None
    assert row["branch"] is None
    # El mensaje lleva el nombre que se buscó, que es lo que hay que corregir.
    assert "yappy-trnxd-payment-aggregator-qa-family" in row["message"]


def test_una_imagen_sin_tag_de_pipeline_no_inventa_pipeline(monkeypatch):
    monkeypatch.setattr(
        ecs_branch,
        "deployed_task_definition",
        lambda cfg, family, service: (39, "ecr/payment-aggregator:latest"),
    )
    monkeypatch.setattr(Config, "with_env", classmethod(lambda cls, env: _Cfg(env=env)))

    row = ecs_branch.environment_deployment("repo", "qa", PARAMETERS)

    assert row["status"] == "no_pipeline_id"
    assert row["image"] == "ecr/payment-aggregator:latest"
    assert row["pipeline_id"] is None


def test_un_ambiente_local_dice_que_no_aplica(monkeypatch):
    """`local` no tiene ECS: no es un fallo, y se dice para que no parezca un hueco."""

    monkeypatch.setattr(
        Config, "with_env", classmethod(lambda cls, env: _Cfg(env="local", is_local=True))
    )
    monkeypatch.setattr(
        ecs_branch,
        "deployed_task_definition",
        lambda *a: pytest.fail("no debería preguntar a ECS por un ambiente local"),
    )

    row = ecs_branch.environment_deployment("repo", "local", PARAMETERS)

    assert row["status"] == "not_applicable"
    assert row["branch"] is None


def test_cada_ambiente_es_independiente_del_otro(monkeypatch):
    """Un ambiente que falla no puede arrastrar al resto de la tabla."""

    def _por_ambiente(cfg, family, service):
        if "prod" in family:
            raise DeploymentUnresolved("no_task_definition", "no existe en prod")
        return (39, "ecr/payment-aggregator:build.210")

    monkeypatch.setattr(ecs_branch, "deployed_task_definition", _por_ambiente)
    monkeypatch.setattr(Config, "with_env", classmethod(lambda cls, env: _Cfg(env=env)))

    qa = ecs_branch.environment_deployment("repo", "qa", PARAMETERS)
    prod = ecs_branch.environment_deployment("repo", "prod", PARAMETERS)

    assert qa["task_definition_revision"] == 39
    assert qa["status"] == "circleci_unavailable"
    assert prod["status"] == "no_task_definition"


def test_la_region_de_la_fila_viene_de_la_config_del_ambiente(monkeypatch):
    monkeypatch.setattr(
        Config,
        "with_env",
        classmethod(lambda cls, env: _Cfg(env=env, region="us-west-2" if env == "prod" else "us-west-1")),
    )

    assert ecs_branch.environment_deployment("repo", "prod", PARAMETERS)["region"] == "us-west-2"
    assert ecs_branch.environment_deployment("repo", "qa", PARAMETERS)["region"] == "us-west-1"

# --- el filtro de ambientes de la ruta -----------------------------------------


def _known(monkeypatch, envs, locals_=()):
    monkeypatch.setattr(Config, "known_environments", classmethod(lambda cls: list(envs)))
    monkeypatch.setattr(
        Config, "with_env", classmethod(lambda cls, env: _Cfg(env=env, is_local=env in locals_))
    )


def test_sin_filtro_pregunta_a_todos_los_ambientes_de_config(monkeypatch):
    _known(monkeypatch, ["dev", "qa", "uat", "local"], locals_=["local"])

    assert deployments._aws_environments() == ["dev", "qa", "uat"]


def test_el_filtro_deja_solo_lo_pedido(monkeypatch):
    """Mirar UAT no debería pagar el viaje a us-west-2 ni a us-east-1."""
    _known(monkeypatch, ["dev", "qa", "uat"])

    assert deployments._aws_environments(["uat"]) == ["uat"]


def test_el_filtro_respeta_el_orden_pedido(monkeypatch):
    """La tabla sale en el orden en que se preguntaron, no en el de la config."""
    _known(monkeypatch, ["dev", "qa", "uat"])

    assert deployments._aws_environments(["uat", "dev"]) == ["uat", "dev"]


def test_un_ambiente_pedido_que_no_existe_entra_para_que_la_fila_lo_diga(monkeypatch):
    """Ignorarlo en silencio haría creer que ese ambiente no tiene nada."""
    _known(monkeypatch, ["dev", "qa"])

    assert deployments._aws_environments(["qa", "stgp"]) == ["qa", "stgp"]


def test_un_ambiente_local_pedido_se_sigue_excluyendo(monkeypatch):
    """Aunque lo pidan, `local` no tiene ECS: la fila lo dice, no le pregunta."""
    _known(monkeypatch, ["qa", "local"], locals_=["local"])

    assert deployments._aws_environments(["qa", "local"]) == ["qa"]


def test_un_filtro_vacio_es_todos_no_ninguno(monkeypatch):
    """`envs: []` tiene que significar lo mismo que no mandar el campo."""
    _known(monkeypatch, ["dev", "qa"])

    assert deployments._aws_environments([]) == ["dev", "qa"]
