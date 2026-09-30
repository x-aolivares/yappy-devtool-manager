"""Tests de la capa HTTP: contrato de endpoints y payload de errores.

Verifica que ningún error de dominio llega al cliente como 500 genérico de
uvicorn, y que el shape `{code, message, detail}` es estable para el frontend.
"""
import pytest
from fastapi.testclient import TestClient

from web.api import container as container_mod
from web.api.container import get_container
from web.api.domain.exceptions import (
    AwsCredentialsError,
    SecretNotFoundError,
    SecretResolutionError,
)
from web.api.main import create_app


class FakeEnvRepo:
    def __init__(self, names=("dev", "qa")):
        self._names = names

    def list_environments(self):
        from web.api.domain.entities import Environment

        return [Environment(name=n, aws_profile="p", aws_region="r") for n in self._names]

    def get_environment(self, name):
        from web.api.domain.entities import Environment

        return Environment(name=name)


class FakeParamRepo:
    def __init__(self, params):
        self._params = params

    def list_parameters(self, env):
        return list(self._params)

    def get_parameter(self, env, key):
        for p in self._params:
            if p.key == key:
                return p
        raise KeyError(key)


@pytest.fixture
def client():
    def build(secrets, params=()):
        app = create_app()
        c = container_mod.Container()
        c.environments = FakeEnvRepo()
        c.parameters = FakeParamRepo(list(params))
        c.secrets = secrets
        # dependency_overrides is the supported override point for Depends()
        app.dependency_overrides[get_container] = lambda: c
        return TestClient(app, raise_server_exceptions=False)

    return build


class StubSecrets:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def resolve(self, env, secret_name):
        self.calls.append((env, secret_name))
        if self.error:
            raise self.error
        return self.result


def test_health(client):
    assert client(StubSecrets()).get("/health").json() == {"status": "ok"}


def test_list_environments(client):
    response = client(StubSecrets()).get("/environments")
    assert response.status_code == 200
    assert [e["name"] for e in response.json()] == ["dev", "qa"]


def test_list_parameters_never_calls_aws(client):
    from web.api.domain.entities import Parameter

    secrets = StubSecrets()
    response = client(
        secrets, params=[Parameter("DB_PORT", "8100", False, "dev")]
    ).get("/parameters/dev")

    assert response.status_code == 200
    assert response.json() == [
        {"key": "DB_PORT", "value": "8100", "is_json": False, "environment": "dev"}
    ]
    assert secrets.calls == [], "listing parameters must not touch AWS"


def test_resolve_returns_secret(client):
    from web.api.domain.entities import Parameter, ResolvedSecret

    secrets = StubSecrets(
        result=ResolvedSecret("prod/db/pw", "s3cr3t", "dev", False)
    )
    response = client(
        secrets, params=[Parameter("DB_SECRET", "prod/db/pw", False, "dev")]
    ).post("/parameters/dev/resolve", json={"key": "DB_SECRET"})

    assert response.status_code == 200
    assert response.json() == {
        "secret_name": "prod/db/pw",
        "value": "s3cr3t",
        "is_json": False,
        "environment": "dev",
    }
    assert secrets.calls == [("dev", "prod/db/pw")]


@pytest.mark.parametrize(
    "error,status,code",
    [
        (SecretNotFoundError("s", "dev"), 404, "SECRET_NOT_FOUND"),
        (AwsCredentialsError("no session"), 401, "AWS_CREDENTIALS_ERROR"),
        (SecretResolutionError("boom"), 502, "SECRET_RESOLUTION_ERROR"),
    ],
)
def test_error_payload_contract(client, error, status, code):
    from web.api.domain.entities import Parameter

    response = client(
        StubSecrets(error=error), params=[Parameter("DB_SECRET", "s", False, "dev")]
    ).post("/parameters/dev/resolve", json={"key": "DB_SECRET"})

    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["message"]
    assert set(body) == {"code", "message", "detail"}


def test_unknown_environment_is_404_with_available_list(client):
    response = client(StubSecrets()).get("/parameters/prod")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "ENVIRONMENT_NOT_FOUND"
    assert "dev" in body["message"]


def test_unexpected_exception_is_wrapped_not_leaked(client):
    from web.api.domain.entities import Parameter

    response = client(
        StubSecrets(error=RuntimeError("psycopg2 exploded")),
        params=[Parameter("DB_SECRET", "s", False, "dev")],
    ).post("/parameters/dev/resolve", json={"key": "DB_SECRET"})

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
