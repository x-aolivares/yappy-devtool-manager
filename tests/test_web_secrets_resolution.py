"""Tests de resolución de secretos: adapter (AWS) + caso de uso + router."""
import json

import pytest
from botocore.exceptions import ClientError

from web.api.application import resolve_secret as us
from web.api.domain import exceptions as exc
from web.api.domain.entities import Environment, Parameter, ResolvedSecret
from web.api.infrastructure import aws_secrets_adapter as adapter_mod
from web.api.infrastructure.aws_secrets_adapter import AwsSecretsAdapter


# --- fakes ---------------------------------------------------------------


class FakeConfig:
    def __init__(self, profile="yappy-dev", region="us-west-2"):
        self.profile = profile
        self.region = region

    @classmethod
    def known_environments(cls):
        return ["dev", "qa"]

    @classmethod
    def with_env(cls, env):
        return cls()


class FakeSecretsClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def get_secret_value(self, SecretId):  # noqa: N803  (AWS casing)
        self.calls.append(SecretId)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def patched(monkeypatch):
    """Wires the adapter to fakes: no AWS, no real config/ dir."""
    monkeypatch.setattr(adapter_mod, "Config", FakeConfig)

    def install(client):
        monkeypatch.setattr(
            AwsSecretsAdapter, "_client", staticmethod(lambda cfg: client)
        )
        return client

    return install


def client_error(code, operation="GetSecretValue"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


# --- adapter -------------------------------------------------------------


def test_resolve_uses_secret_name_verbatim(patched):
    client = patched(FakeSecretsClient(response={"SecretString": "hunter2"}))

    resolved = AwsSecretsAdapter().resolve("dev", "prod/db/orders-password")

    assert client.calls == ["prod/db/orders-password"]
    assert resolved.value == "hunter2"
    assert resolved.environment == "dev"
    assert resolved.secret_name == "prod/db/orders-password"
    assert resolved.is_json is False


def test_resolve_flags_json_payload(patched):
    patched(FakeSecretsClient(response={"SecretString": '{"user":"root","pass":"s3cr3t"}'}))

    resolved = AwsSecretsAdapter().resolve("dev", "prod/db/creds")

    assert resolved.is_json is True
    assert json.loads(resolved.value)["user"] == "root"


def test_resolve_decodes_binary_secret(patched):
    import base64

    encoded = base64.b64encode(b"plain-text-secret").decode()
    patched(FakeSecretsClient(response={"SecretBinary": encoded}))

    assert AwsSecretsAdapter().resolve("dev", "s").value == "plain-text-secret"


def test_resolve_rejects_empty_secret_name(patched):
    client = patched(FakeSecretsClient(response={"SecretString": "x"}))

    with pytest.raises(exc.SecretResolutionError):
        AwsSecretsAdapter().resolve("dev", "   ")

    assert client.calls == [], "must not hit AWS with an empty name"


def test_unknown_environment_never_reaches_aws(patched):
    client = patched(FakeSecretsClient(response={"SecretString": "x"}))

    with pytest.raises(exc.EnvironmentNotFoundError):
        AwsSecretsAdapter().resolve("prod", "s")

    assert client.calls == []


def test_missing_secret_maps_to_404(patched):
    patched(FakeSecretsClient(error=client_error("ResourceNotFoundException")))

    with pytest.raises(exc.SecretNotFoundError) as info:
        AwsSecretsAdapter().resolve("dev", "nope")

    assert info.value.code == "SECRET_NOT_FOUND"
    assert "nope" in str(info.value)
    # inherits SecretResolutionError -> the 502 default still catches it
    assert isinstance(info.value, exc.SecretResolutionError)


@pytest.mark.parametrize(
    "code",
    [
        "AccessDeniedException",
        "ExpiredTokenException",
        "UnrecognizedClientException",
    ],
)
def test_auth_failures_map_to_credentials_error(patched, code):
    patched(FakeSecretsClient(error=client_error(code)))

    with pytest.raises(exc.AwsCredentialsError) as info:
        AwsSecretsAdapter().resolve("dev", "s")

    assert info.value.code == "AWS_CREDENTIALS_ERROR"
    assert "yappy login aws" in str(info.value)


def test_other_client_errors_map_to_secret_resolution_error(patched):
    patched(FakeSecretsClient(error=client_error("ThrottlingException")))

    with pytest.raises(exc.SecretResolutionError) as info:
        AwsSecretsAdapter().resolve("dev", "s")

    assert type(info.value) is exc.SecretResolutionError
    assert "ThrottlingException" in str(info.value)


def test_response_without_value_is_an_error_not_a_silent_empty(patched):
    patched(FakeSecretsClient(response={"Name": "s"}))

    with pytest.raises(exc.SecretResolutionError):
        AwsSecretsAdapter().resolve("dev", "s")


# --- use case ------------------------------------------------------------


class FakeEnvRepo:
    def __init__(self, names=("dev", "qa")):
        self._names = names

    def list_environments(self):
        return [Environment(name=n) for n in self._names]

    def get_environment(self, name):
        raise NotImplementedError


class FakeParamRepo:
    def __init__(self, params):
        self._params = params

    def list_parameters(self, env):
        return list(self._params)

    def get_parameter(self, env, key):
        return us._find_parameter(FakeEnvRepo(), self, env, key)


class RecordingResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, env, secret_name):
        self.calls.append((env, secret_name))
        return ResolvedSecret(secret_name, "resolved", env, False)


def test_use_case_passes_parameter_value_as_secret_name():
    resolver = RecordingResolver()
    repo = FakeParamRepo([Parameter("DB_SECRET", "prod/db/pw", False, "dev")])

    result = us.resolve_secret(FakeEnvRepo(), repo, resolver, "dev", "DB_SECRET")

    assert resolver.calls == [("dev", "prod/db/pw")]
    assert result.value == "resolved"


def test_use_case_does_not_resolve_when_parameter_missing():
    resolver = RecordingResolver()
    repo = FakeParamRepo([Parameter("OTHER", "x", False, "dev")])

    with pytest.raises(exc.ParameterNotFoundError):
        us.resolve_secret(FakeEnvRepo(), repo, resolver, "dev", "DB_SECRET")

    assert resolver.calls == [], "must not call AWS for a parameter that does not exist"


def test_use_case_validates_environment_before_anything():
    resolver = RecordingResolver()
    repo = FakeParamRepo([Parameter("DB_SECRET", "x", False, "dev")])

    with pytest.raises(exc.EnvironmentNotFoundError):
        us.resolve_secret(FakeEnvRepo(), repo, resolver, "prod", "DB_SECRET")

    assert resolver.calls == []
