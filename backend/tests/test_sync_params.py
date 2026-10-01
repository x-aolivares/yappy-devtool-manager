import json

from yappy_library.application.database.sync import params as p


def test_compare_values_equal_json():
    status, is_json, changes, patch = p.compare_values('{"a": 1}', '{"a": 1}')
    assert status == "equal"
    assert is_json is True
    assert changes == []
    assert patch == '{"a": 1}'


def test_compare_values_partial_patch_only_changes_changed_keys():
    a = '{"common": "keep", "rate": 10, "nested": {"x": 1, "y": 2}}'
    b = '{"common": "keep", "rate": 20, "nested": {"x": 1, "y": 9}, "new_key": 1}'

    status, is_json, changes, patch = p.compare_values(a, b)

    assert status == "different"
    assert is_json is True
    patched = json.loads(patch)
    assert patched["common"] == "keep"  # untouched
    assert patched["rate"] == 20
    assert patched["nested"] == {"x": 1, "y": 9}
    assert patched["new_key"] == 1

    paths = {c["path"] for c in changes}
    assert "$.rate" in paths
    assert "$.nested.y" in paths
    assert "$.new_key" in paths
    assert "$.common" not in paths


def test_compare_values_removes_keys_present_only_in_a():
    a = '{"a": 1, "gone": 2}'
    b = '{"a": 1}'

    status, is_json, changes, patch = p.compare_values(a, b)

    assert status == "different"
    assert json.loads(patch) == {"a": 1}
    assert any(c["path"] == "$.gone" and c["op"] == "del" for c in changes)


def test_compare_values_list_patch():
    a = '{"items": ["a", "b", "x"]}'
    b = '{"items": ["a", "b", "c"]}'

    status, is_json, changes, patch = p.compare_values(a, b)

    assert status == "different"
    assert json.loads(patch) == {"items": ["a", "b", "c"]}


def test_compare_values_plain_strings():
    status, is_json, changes, patch = p.compare_values("abc", "abd")
    assert status == "different"
    assert is_json is False
    assert patch == "abd"
    assert changes == [{"path": "$", "op": "set", "old": "abc", "new": "abd"}]


def test_compare_values_equal_plain_strings():
    status, is_json, changes, patch = p.compare_values("abc", "abc")
    assert status == "equal"
    assert is_json is False
    assert changes == []


def test_compare_values_one_side_not_json_treated_as_string():
    status, is_json, changes, patch = p.compare_values("{not: json", '{"rate": 20}')
    assert status == "different"
    assert is_json is False
    assert patch == '{"rate": 20}'


def test_shell_quote_escapes_single_quotes():
    assert p._shell_quote("a'b") == "'a'\\''b'"


def test_build_ssm_script():
    class FakeCfg:
        profile = "dev-profile"
        region = "us-west-2"

    script = p.build_ssm_script("/app/rate", "20", "String", FakeCfg())

    assert "--profile 'dev-profile'" in script
    assert "--region 'us-west-2'" in script
    assert "--type String" in script
    assert "--overwrite" in script
    assert "--name '/app/rate'" in script


def test_build_secret_script():
    class FakeCfg:
        profile = "qa-profile"
        region = "us-west-1"

    script = p.build_secret_script("/app/secret", '{"a": 1}', FakeCfg())

    assert script.startswith("aws secretsmanager update-secret")
    assert "--secret-id '/app/secret'" in script
    assert "--profile 'qa-profile'" in script
    assert "--region 'us-west-1'" in script


def test_build_ssm_delete_script():
    class FakeCfg:
        profile = "dev-profile"
        region = "us-west-2"

    script = p.build_ssm_delete_script("/app/rate", FakeCfg())

    assert script.startswith("aws ssm delete-parameter")
    assert "--name '/app/rate'" in script
    assert "--profile 'dev-profile'" in script
    assert "--region 'us-west-2'" in script


def test_build_secret_delete_script():
    class FakeCfg:
        profile = "qa-profile"
        region = "us-west-1"

    script = p.build_secret_delete_script("/app/secret", FakeCfg())

    assert script.startswith("aws secretsmanager delete-secret")
    assert "--secret-id '/app/secret'" in script
    assert "--force-delete-without-recovery" in script
    assert "--profile 'qa-profile'" in script
    assert "--region 'us-west-1'" in script


class _FakeClient:
    class ResourceNotFoundException(Exception):
        pass

    exceptions = type(
        "Exceptions",
        (),
        {"ResourceNotFoundException": ResourceNotFoundException},
    )()

    def __init__(self, service):
        self.service = service
        self.calls = []

    def put_parameter(self, **kwargs):
        self.calls.append(kwargs)
        return {"Version": 7}

    def delete_parameter(self, **kwargs):
        self.calls.append(kwargs)
        return {}

    def update_secret(self, **kwargs):
        self.calls.append(kwargs)
        return {}

    def create_secret(self, **kwargs):
        self.calls.append(kwargs)
        return {}

    def delete_secret(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class _FakeSession:
    def __init__(self, client):
        self._client = client

    def client(self, service):
        return self._client


def test_put_parameter_overwrites_with_type(monkeypatch):
    client = _FakeClient("ssm")
    monkeypatch.setattr(p, "_boto_session", lambda profile, region: _FakeSession(client))

    cfg = type("Cfg", (), {"profile": "prof-dev", "region": "us-east-1"})()
    result = p.put_parameter(cfg, "/x", "20", "SecureString")

    kwargs = client.calls[0]
    assert kwargs["Name"] == "/x"
    assert kwargs["Value"] == "20"
    assert kwargs["Type"] == "SecureString"
    assert kwargs["Overwrite"] is True
    assert result["message"] == "Parámetro '/x' actualizado en us-east-1 (versión 7)."


def test_update_secret_updates_and_creates_when_missing(monkeypatch):
    client = _FakeClient("secretsmanager")
    monkeypatch.setattr(p, "_boto_session", lambda profile, region: _FakeSession(client))

    def raiser(*a, **k):
        raise _FakeClient.ResourceNotFoundException("a")

    cfg = type("Cfg", (), {"profile": "prof-qa", "region": "us-west-2"})()

    result = p.update_secret(cfg, "/s", "pepitos")
    assert client.calls[0]["SecretString"] == "pepitos"
    assert "actualizado" in result["message"]

    client.calls.clear()
    client.update_secret = raiser
    result = p.update_secret(cfg, "/s", "papitas")
    assert client.calls[0]["Name"] == "/s"
    assert "se creó" in result["message"]


def test_delete_parameter_and_secret(monkeypatch):
    client = _FakeClient("ssm")
    monkeypatch.setattr(p, "_boto_session", lambda profile, region: _FakeSession(client))

    cfg = type("Cfg", (), {"profile": "prof-dev", "region": "us-east-1"})()
    result = p.delete_parameter(cfg, "/x")
    assert client.calls[0]["Name"] == "/x"
    assert "eliminado" in result["message"]

    client2 = _FakeClient("secretsmanager")
    monkeypatch.setattr(p, "_boto_session", lambda profile, region: _FakeSession(client2))
    result = p.delete_secret(cfg, "/s")
    assert client2.calls[0]["SecretId"] == "/s"
    assert client2.calls[0]["ForceDeleteWithoutRecovery"] is True
    assert "eliminado" in result["message"]


def test_diff_params_missing_in_a(monkeypatch):
    class FakeCfg:
        profile = "a-profile"
        region = "us-east-1"

        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(
        p, "read_parameter",
        lambda cfg, name: (_raise_not_found() if cfg.name == "a" else ("20", "String")),
    )

    class FakeA(FakeCfg):
        pass

    class FakeB(FakeCfg):
        pass

    def _raise_not_found():
        raise p.ParamNotFound("x")

    result = p.diff_params(FakeA("a"), FakeB("b"), "ssm", "/app/rate", "a", "b")

    assert result.status == "missing_in_a"
    assert result.value_b == "20"
    assert "aws ssm put-parameter" in result.script
    assert "--profile 'a-profile'" in result.script


def test_diff_params_equal(monkeypatch):
    def fake_read(cfg, name):
        return ("same-value", "String")

    monkeypatch.setattr(p, "read_parameter", fake_read)

    class FakeCfg:
        profile = "p"
        region = "r"

    result = p.diff_params(FakeCfg(), FakeCfg(), "ssm", "/x", "a", "b")

    assert result.status == "equal"
    assert result.script is None
    assert result.value_a == "same-value"


def _patch_reads(monkeypatch, absent):
    """fake_any returns a value (with type) except for the absent region."""

    def fake_any(cfg, name):
        if cfg.name == absent:
            raise p.ParamNotFound(name)
        return ("20", "String")

    monkeypatch.setattr(p, "read_parameter", fake_any)
    monkeypatch.setattr(
        p, "read_secret", lambda cfg, name: fake_any(cfg, name)[0]
    )


def test_diff_params_missing_in_b_reports_only_by_default(monkeypatch):
    class FakeCfg:
        profile = "a-profile"
        region = "us-east-1"

        def __init__(self, name):
            self.name = name

    _patch_reads(monkeypatch, absent="b")
    cfg_a, cfg_b = FakeCfg("a"), FakeCfg("b")

    result = p.diff_params(cfg_a, cfg_b, "ssm", "/x", "a", "b")

    assert result.status == "missing_in_b"
    assert result.value_a == "20"
    assert result.script is None
    assert "no hay nada que sincronizar" in result.notes[0]


def test_diff_params_missing_in_b_generates_delete_script(monkeypatch):
    class FakeCfg:
        profile = "a-profile"
        region = "us-east-1"

        def __init__(self, name):
            self.name = name

    _patch_reads(monkeypatch, absent="b")
    cfg_a, cfg_b = FakeCfg("a"), FakeCfg("b")

    ssm = p.diff_params(cfg_a, cfg_b, "ssm", "/x", "a", "b", include_deletes=True)
    secret = p.diff_params(
        cfg_a, cfg_b, "secretsmanager", "/s", "a", "b", include_deletes=True
    )

    assert ssm.script.startswith("aws ssm delete-parameter")
    assert "--name '/x'" in ssm.script
    assert "--profile 'a-profile'" in ssm.script
    assert "se elimina" in ssm.notes[0]

    assert secret.script.startswith("aws secretsmanager delete-secret")
    assert "--secret-id '/s'" in secret.script
    assert "--force-delete-without-recovery" in secret.script


class _PairCfg:
    profile = "a-profile"
    region = "us-east-1"

    def __init__(self, name):
        self.name = name


def _pair_reads(monkeypatch, param_a=None, param_b=None, secret_a=None, secret_b=None):
    def fake_param(cfg, name):
        v = param_a if cfg.name == "a" else param_b
        if v is None:
            raise p.ParamNotFound(name)
        return (v, "SecureString")

    def fake_secret(cfg, name):
        v = secret_a if cfg.name == "a" else secret_b
        if v is None:
            raise p.ParamNotFound(name)
        return v

    monkeypatch.setattr(p, "read_parameter", fake_param)
    monkeypatch.setattr(p, "read_secret", fake_secret)


def test_diff_params_pair_secret_first_then_parameter(monkeypatch):
    """Ejemplo del usuario: param value y secret-value cambian; secreto va primero."""
    _pair_reads(
        monkeypatch,
        param_a="this-is-provider-password", param_b="this-is-new-password",
        secret_a="prrito$14", secret_b="prrito$2026",
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/abc/def/jklmn", "a", "b")

    assert result.pair
    assert result.status == "different"
    assert result.param_apply == "this-is-new-password"
    assert result.secret_apply == "prrito$2026"
    assert [s["step"] for s in result.steps] == ["secreto", "parámetro"]
    assert "update-secret" in result.steps[0]["command"]
    assert "put-parameter" in result.steps[1]["command"]
    assert "secreto" in result.notes[0]


def test_diff_params_pair_only_secret_differs(monkeypatch):
    _pair_reads(
        monkeypatch,
        param_a="same", param_b="same",
        secret_a="old", secret_b="new",
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/s", "a", "b")

    assert result.status == "different"
    assert result.param_needs_write is False
    assert result.secret_needs_write is True
    assert [s["step"] for s in result.steps] == ["secreto"]


def test_diff_params_pair_equal(monkeypatch):
    _pair_reads(
        monkeypatch,
        param_a="v", param_b="v", secret_a="s", secret_b="s",
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/x", "a", "b")

    assert result.status == "equal"
    assert result.param_needs_write is False
    assert result.secret_needs_write is False
    assert result.steps == []
    assert result.script is None


def test_diff_params_pair_missing_in_a_creates_both(monkeypatch):
    _pair_reads(
        monkeypatch,
        param_a=None, param_b="p", secret_a=None, secret_b="s",
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/x", "a", "b")

    assert result.status == "missing_in_a"
    assert result.param_status == "missing_in_a"
    assert result.secret_status == "missing_in_a"
    assert [s["step"] for s in result.steps] == ["secreto", "parámetro"]
    assert "crear" in result.notes[0]


def test_diff_params_pair_missing_in_b_never_deletes(monkeypatch):
    _pair_reads(
        monkeypatch,
        param_a="p", param_b=None, secret_a="s", secret_b=None,
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/x", "a", "b")

    assert result.status == "missing_in_b"
    assert result.script is None
    assert result.steps == []
    assert "no se elimina" in result.notes[0]


def test_diff_params_pair_none_nowhere(monkeypatch):
    _pair_reads(monkeypatch, param_a=None, param_b=None, secret_a=None, secret_b=None)
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/x", "a", "b")

    assert result.status == "none"


def test_diff_params_pair_detects_secret_reference_in_parameter_value(monkeypatch):
    _pair_reads(
        monkeypatch,
        param_a="/prod/secret/db-password",
        param_b="/prod/secret/db-password-qa",
        secret_a="legacy-secret-a",
        secret_b="legacy-secret-b",
    )
    result = p.diff_params_pair(
        _PairCfg("a"), _PairCfg("b"), "/prod/network/ecs/cluster_name", "a", "b"
    )

    assert result.pair is True
    assert result.status == "different"
    assert result.secret_needs_write is True
    assert result.param_apply == "/prod/secret/db-password-qa"
    assert result.secret_apply == "legacy-secret-b"


def test_diff_params_pair_json_param_uses_patch(monkeypatch):
    import json

    _pair_reads(
        monkeypatch,
        param_a='{"a": 1}', param_b='{"a": 1, "b": 2}',
        secret_a="same", secret_b="same",
    )
    result = p.diff_params_pair(_PairCfg("a"), _PairCfg("b"), "/j", "a", "b")

    assert result.secret_needs_write is False
    assert result.param_needs_write is True
    assert result.param_status == "different"
    # el patch JSON fusiona el valor del origen sin tocar el resto
    assert json.loads(result.param_apply) == {"a": 1, "b": 2}
