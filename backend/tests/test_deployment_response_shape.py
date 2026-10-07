"""El response tiene que coincidir con lo que CircleCI devuelve de verdad.

Reproduce la forma real del `vcs.commit` de la API v2 —un objeto con `body` y
`subject`, no el SHA— que es lo que rompía la validación del response con 500.
"""

from __future__ import annotations

import pytest

from yappy_api.schemas import DeploymentEnvInfo
from yappy_library.application.deployment import ecs_branch

REAL_SHAPE = {
    "vcs": {
        "provider_name": "Bitbucket",
        "origin_repository_url": "https://bitbucket.org/bg-ti/repo",
        "target_repository_url": "https://bitbucket.org/bg-ti/repo",
        "revision": "ca67134f650e362133e51a9ffdb8e5ddc7fa53a5",
        "commit": {
            "body": "",
            "subject": "remove(YNC-3912): validacion de tipo marketplace para el mcm_uuid del aggregator",
        },
        "branch": "main",
    },
    "url": "https://app.circleci.com/pipelines/bb/bg-ti/repo/210",
    "number": 210,
}


@pytest.fixture
def circleci_real(monkeypatch):
    def install(payload=None):
        import json

        monkeypatch.setattr(
            ecs_branch, "_http_get", lambda url, headers: json.dumps(payload or REAL_SHAPE)
        )

    return install


def _row(**overrides) -> dict:
    row = {
        "env": "qa",
        "region": "us-west-1",
        "status": "ok",
        "task_definition_family": "yappy-trnxd-payment-aggregator-qa-family",
        "task_definition_revision": 39,
        "image": "ecr/payment-aggregator:build.210",
        "pipeline_id": 210,
        "branch": None,
        "commit": None,
        "commit_subject": None,
        "pipeline_url": None,
        "message": None,
    }
    row.update(overrides)
    return row


def test_la_fila_valida_contra_el_schema(circleci_real):
    """El 500 venía de acá: el modelo decía string y CircleCI manda un dict."""

    circleci_real()
    source = ecs_branch.pipeline_source(
        type("C", (), {"get": lambda self, k, d=None: "t"})(), "repo", 210
    )

    DeploymentEnvInfo(**_row(**source))  # no debe lanzar


def test_el_sujeto_del_commit_sobrevive_al_schema(circleci_real):
    circleci_real()
    source = ecs_branch.pipeline_source(
        type("C", (), {"get": lambda self, k, d=None: "t"})(), "repo", 210
    )

    model = DeploymentEnvInfo(**_row(**source))

    assert model.commit == "ca67134f650e362133e51a9ffdb8e5ddc7fa53a5"
    assert "YNC-3912" in model.commit_subject