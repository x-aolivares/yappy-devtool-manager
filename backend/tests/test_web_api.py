import pytest
from fastapi import HTTPException

from yappy_api.routes.db import api_db_diff, api_db_schemas
from yappy_api.routes.envs import api_envs
from yappy_api.routes.params import (
    api_params_apply,
    api_params_apply_execute,
    api_params_diff,
    api_params_get,
    api_params_multi,
    api_params_read,
)
from yappy_api.routes.sessions import (
    api_sessions_create,
    api_sessions_delete,
    api_sessions_get,
    api_sessions_item_update,
    api_sessions_list,
)
from yappy_api.schemas import (
    ApplyParamsRequest,
    CreateMultiParamsRequest,
    CreateSessionRequest,
    DbDiffRequest,
    ExecuteParamsRequest,
    ParamsDiffRequest,
    ReadParamsEntry,
    UpdateSessionItemRequest,
)
from yappy_library.adapters.database.connection import SyncError
from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import params as p
from yappy_library.config import Config


class _FakeConfig:
    def __init__(self, env):
        self._env = env

    @property
    def region(self):
        return "us-east-1"

    @property
    def profile(self):
        return "prof-" + self._env


def _fake_with_env(env):
    if env == "broken":
        raise ValueError("Missing required config: AWS_REGION")
    return _FakeConfig(env)


def test_api_envs_keeps_envs_and_reports_load_errors(monkeypatch):
    monkeypatch.setattr(Config, "_get_config_dir", classmethod(lambda cls: None))
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "broken"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(_fake_with_env))

    payload = api_envs()

    assert payload["config_dir"] == "None"  # _get_config_dir mocked -> str(None)
    assert len(payload["environments"]) == 2
    by_env = {e["env"]: e for e in payload["environments"]}

    assert by_env["dev"]["load_error"] is None
    assert by_env["dev"]["profile"] == "prof-dev"
    assert by_env["dev"]["region"] == "us-east-1"

    assert by_env["broken"]["load_error"] == "Missing required config: AWS_REGION"
    assert by_env["broken"]["profile"] is None
    assert by_env["broken"]["region"] is None


def _fake_read_many(cfg, entries):
    return [
        {
            "key": e["key"],
            "is_secret": e["is_secret"],
            "service": "secretsmanager" if e["is_secret"] else "ssm",
            "value": "valor-" + e["key"],
            "value_type": None,
            "ok": True,
            "error": None,
        }
        for e in entries
    ]


def test_api_params_read_ok(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr(p, "read_many", staticmethod(_fake_read_many))

    payload = api_params_read(
        env="dev",
        body=[
            ReadParamsEntry(key="/rate"),
            ReadParamsEntry(key="/secret", is_secret=True),
        ],
    )

    assert payload["env"] == "dev"
    assert payload["ok_count"] == 2 and payload["err_count"] == 0
    assert payload["results"][0]["key"] == "/rate"
    assert payload["results"][1]["service"] == "secretsmanager"


def test_api_params_read_accepts_plain_key_strings(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    received = []
    monkeypatch.setattr(
        p,
        "read_many",
        staticmethod(lambda cfg, entries: received.append(entries) or []),
    )

    payload = api_params_read(
        env="dev",
        body=["/prod/ecommerce/db/master_url", "/prod/payment/stripe/secret_key"],
    )

    assert received == [
        ["/prod/ecommerce/db/master_url", "/prod/payment/stripe/secret_key"]
    ]
    assert payload["ok_count"] == len(payload["results"]) == 0
    assert payload["err_count"] == 0


def test_api_params_read_empty_list_returns_400(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_read(env="dev", body=[])

    assert exc_info.value.status_code == 400
    assert "vacía" in str(exc_info.value.detail)


# --- Sessions API ---


def test_api_sessions_create_and_get(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("YAPPY_SESSIONS_DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    created = api_sessions_create(
        CreateSessionRequest(env_a="dev", env_b="qa", keys=["/a", "/b"])
    )
    assert created["env_a"] == "dev" and created["env_b"] == "qa"
    assert len(created["items"]) == 2

    got = api_sessions_get(session_id=created["id"])
    assert [i["name"] for i in got["items"]] == ["/a", "/b"]

    listed = api_sessions_list()
    assert listed["sessions"][0]["id"] == created["id"]


def test_api_sessions_create_validation(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    with pytest.raises(HTTPException) as exc_info:
        api_sessions_create(
            CreateSessionRequest(env_a="dev", env_b="dev", keys=["/a"])
        )
    assert exc_info.value.status_code == 400


def test_api_sessions_item_update(monkeypatch, tmp_path):
    monkeypatch.setenv("YAPPY_SESSIONS_DB", str(tmp_path / "s.db"))
    created = api_sessions_create(
        CreateSessionRequest(env_a="dev", env_b="qa", keys=["/a"])
    )

    item = api_sessions_item_update(
        session_id=created["id"],
        req=UpdateSessionItemRequest(name="/a", status="aplicado", script="aws ssm ..."),
    )
    assert item["status"] == "aplicado"
    assert item["script"] == "aws ssm ..."

    got = api_sessions_get(session_id=created["id"])
    assert got["status_counts"]["aplicado"] == 1

    with pytest.raises(HTTPException) as exc_info:
        api_sessions_item_update(
            session_id=created["id"],
            req=UpdateSessionItemRequest(name="/a", status="invalido"),
        )
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        api_sessions_item_update(
            session_id=created["id"],
            req=UpdateSessionItemRequest(name="/no-existe", status="aplicado"),
        )
    assert exc_info.value.status_code == 404


def test_api_sessions_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("YAPPY_SESSIONS_DB", str(tmp_path / "s.db"))
    created = api_sessions_create(
        CreateSessionRequest(env_a="dev", env_b="qa", keys=["/a"])
    )
    assert api_sessions_delete(session_id=created["id"]) == {"ok": True}

    with pytest.raises(HTTPException) as exc_info:
        api_sessions_get(session_id=created["id"])
    assert exc_info.value.status_code == 404


class _CaptureDiff:
    def __init__(self, fake_result):
        self.fake_result = fake_result
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.fake_result


def test_api_params_diff_forwards_include_deletes(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    base = p.ParamDiffResult(
        env_a="dev", env_b="qa", service="ssm", name="/x",
        status="missing_in_b", value_a="20", value_type_a="String",
    )
    capture = _CaptureDiff(base)
    monkeypatch.setattr(p, "diff_params", capture)

    api_params_diff(
        ParamsDiffRequest(env_a="dev", env_b="qa", service="ssm", name="/x")
    )
    assert capture.kwargs["include_deletes"] is False

    payload = api_params_diff(
        ParamsDiffRequest(
            env_a="dev", env_b="qa", service="ssm", name="/x",
            include_deletes=True,
        )
    )
    assert capture.kwargs["include_deletes"] is True
    assert payload["status"] == "missing_in_b"


def test_api_params_diff_with_secret_forwards_to_pair(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}
    monkeypatch.setattr(
        p, "diff_params_pair",
        lambda cfg_a, cfg_b, name, env_a, env_b: captured.update(
            name=name, env_a=env_a, env_b=env_b, cfg_a=cfg_a._env, cfg_b=cfg_b._env
        ) or p.ParamDiffResult(
            env_a=env_a, env_b=env_b, service="ssm", name=name,
            status="different", pair=True, param_needs_write=True,
            secret_needs_write=True,
        ),
    )

    payload = api_params_diff(
        ParamsDiffRequest(
            env_a="dev", env_b="qa", service="ssm", name="/abc", with_secret=True,
        )
    )
    assert captured == {"name": "/abc", "env_a": "dev", "env_b": "qa", "cfg_a": "dev", "cfg_b": "qa"}
    assert payload["pair"] is True


def test_api_params_diff_with_secret_rejected_for_secretsmanager(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_diff(
            ParamsDiffRequest(
                env_a="dev", env_b="qa", service="secretsmanager",
                name="/abc", with_secret=True,
            )
        )
    assert exc_info.value.status_code == 400
    assert "SSM" in str(exc_info.value.detail)


def test_api_params_diff_auto_enters_pair_when_secret_exists(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr(
        p,
        "read_secret",
        staticmethod(lambda cfg, name: "valor-del-secreto"),
    )

    captured = {}
    monkeypatch.setattr(
        p, "diff_params",
        lambda cfg_a, cfg_b, service, name, env_a, env_b, **kwargs: p.ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="different", value_a="param-a", value_b="param-b",
        ),
    )
    monkeypatch.setattr(
        p, "diff_params_pair",
        lambda cfg_a, cfg_b, name, env_a, env_b: captured.update(
            name=name, env_a=env_a, env_b=env_b,
        ) or p.ParamDiffResult(
            env_a=env_a, env_b=env_b, service="ssm", name=name,
            status="different", pair=True, param_needs_write=True,
            secret_needs_write=True, secret_value_a="secret-a",
            secret_value_b="secret-b", notes=["Hay cambios."],
        ),
    )

    payload = api_params_diff(
        ParamsDiffRequest(env_a="dev", env_b="qa", service="ssm", name="/abc")
    )

    assert captured == {"name": "/abc", "env_a": "dev", "env_b": "qa"}
    assert payload["pair"] is True
    assert payload["secret_value_a"] == "secret-a"
    assert any("mismo nombre" in n for n in payload["notes"])


def test_api_params_diff_no_auto_pair_when_no_secret(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr(
        p, "read_secret",
        staticmethod(lambda cfg, name: (_ for _ in ()).throw(p.ParamNotFound(name))),
    )

    calls = []
    monkeypatch.setattr(
        p, "diff_params",
        lambda cfg_a, cfg_b, service, name, env_a, env_b, **kwargs: calls.append(
            (service, name, env_a, env_b)
        ) or p.ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="equal", value_a="v", value_b="v",
        ),
    )
    monkeypatch.setattr(
        p, "diff_params_pair",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debería llamarse")),
    )

    payload = api_params_diff(
        ParamsDiffRequest(env_a="dev", env_b="qa", service="ssm", name="/abc")
    )

    assert calls == [("ssm", "/abc", "dev", "qa")]
    assert payload["pair"] is False


def test_api_params_diff_no_auto_pair_for_secretsmanager_service(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr(
        p, "read_secret",
        staticmethod(lambda cfg, name: "valor-del-secreto"),
    )

    calls = []
    monkeypatch.setattr(
        p, "diff_params",
        lambda cfg_a, cfg_b, service, name, env_a, env_b, **kwargs: calls.append(
            service
        ) or p.ParamDiffResult(
            env_a=env_a, env_b=env_b, service=service, name=name,
            status="equal", value_a="v", value_b="v",
        ),
    )
    monkeypatch.setattr(
        p, "diff_params_pair",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debería llamarse")),
    )

    api_params_diff(
        ParamsDiffRequest(
            env_a="dev", env_b="qa", service="secretsmanager", name="/abc"
        )
    )

    assert calls == ["secretsmanager"]


def test_api_params_apply_with_secret_builds_steps_in_order(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = []
    monkeypatch.setattr(
        p, "build_secret_script",
        lambda name, value, cfg: captured.append(("secret", name, value)) or "SECRET SCRIPT",
    )
    monkeypatch.setattr(
        p, "build_ssm_script",
        lambda name, value, value_type, cfg: captured.append(("param", name, value, value_type)) or "PARAM SCRIPT",
    )

    payload = api_params_apply(
        ApplyParamsRequest(
            env_a="dev", env_b="qa", service="ssm", name="/abc",
            new_value="this-is-new-password", value_type="SecureString",
            with_secret=True, new_secret_value="prrito$2026",
            write_secret=True, write_param=True,
        )
    )

    assert [c[0] for c in captured] == ["secret", "param"]
    assert payload["script"] == "SECRET SCRIPT\n\nPARAM SCRIPT"
    assert [s["step"] for s in payload["steps"]] == ["secreto", "parámetro"]


def test_api_params_apply_execute_with_secret_runs_secret_then_param(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "update_secret",
        lambda cfg, name, value: calls.append(("secret", cfg._env, name, value))
        or {"ok": True, "message": "secreto ok"},
    )
    monkeypatch.setattr(
        p, "put_parameter",
        lambda cfg, name, value, value_type: calls.append(("param", cfg._env, name, value, value_type))
        or {"ok": True, "message": "param ok"},
    )

    payload = api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="dev", env_b="qa", service="ssm", op="update", name="/abc",
            new_value="this-is-new-password", value_type="SecureString",
            with_secret=True, new_secret_value="prrito$2026",
            write_secret=True, write_param=True, confirm=True,
        )
    )

    assert [c[0] for c in calls] == ["secret", "param"]
    assert calls[0][1:] == ("dev", "/abc", "prrito$2026")
    assert "secreto ok" in payload["message"]
    assert payload["steps"][0]["step"] == "secreto"
    assert payload["steps"][1]["step"] == "parámetro"


def test_api_params_apply_execute_with_secret_aborts_param_when_secret_fails(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "update_secret",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secreto falló")),
    )
    monkeypatch.setattr(
        p, "put_parameter",
        lambda *a, **k: calls.append("param") or {"ok": True, "message": "param ok"},
    )

    with pytest.raises(HTTPException) as exc_info:
        api_params_apply_execute(
            ExecuteParamsRequest(
                env_a="dev", env_b="qa", service="ssm", op="update", name="/abc",
                with_secret=True, new_secret_value="x",
                write_secret=True, write_param=True, confirm=True,
            )
        )
    assert exc_info.value.status_code == 400
    assert "secreto falló" in str(exc_info.value.detail)
    assert calls == []  # el parámetro NO se toca si el secreto falló


def test_api_params_apply_execute_with_secret_delete_rejected(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_apply_execute(
            ExecuteParamsRequest(
                env_a="dev", env_b="qa", service="ssm", op="delete", name="/abc",
                with_secret=True, confirm=True,
            )
        )
    assert exc_info.value.status_code == 400
    assert "no admite eliminaciones" in str(exc_info.value.detail)


def test_api_params_apply_builds_ssm_script(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    payload = api_params_apply(
        ApplyParamsRequest(
            env_a="dev", env_b="qa", service="ssm", name="/x",
            new_value='{"name": 1}', value_type="SecureString",
        )
    )

    assert payload["script"].startswith("aws ssm put-parameter")
    assert "--type SecureString" in payload["script"]
    assert "--profile 'prof-dev'" in payload["script"]
    assert "'{\"name\": 1}'" in payload["script"]


def test_api_params_apply_builds_secret_script(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    payload = api_params_apply(
        ApplyParamsRequest(
            env_a="qa", env_b="dev", service="secretsmanager", name="/s",
            new_value="pepitos",
        )
    )

    assert payload["script"].startswith("aws secretsmanager update-secret")
    assert "--secret-id '/s'" in payload["script"]
    assert "'pepitos'" in payload["script"]
    assert "--profile 'prof-qa'" in payload["script"]


def test_api_params_apply_execute_requires_confirmation(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_apply_execute(
            ExecuteParamsRequest(
                env_a="dev", env_b="qa", service="ssm", name="/x",
                new_value="20", confirm=False,
            )
        )
    assert exc_info.value.status_code == 400
    assert "confirmación" in str(exc_info.value.detail)


def test_api_params_apply_execute_forwards_to_ssm_put(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}

    def fake_put(cfg, name, value, value_type):
        captured["cfg_env"] = cfg._env
        captured["value"] = value
        captured["value_type"] = value_type
        return {"ok": True, "message": "listo"}

    monkeypatch.setattr(p, "put_parameter", fake_put)

    payload = api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="qa", env_b="dev", service="ssm", name="/x",
            new_value="20", value_type="SecureString", confirm=True,
        )
    )

    assert captured["cfg_env"] == "qa"
    assert captured["value"] == "20"
    assert captured["value_type"] == "SecureString"
    assert payload["ok"] is True
    assert payload["message"] == "listo"


def test_api_params_apply_execute_forwards_to_secret_update(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}

    def fake_update(cfg, name, value):
        captured["name"] = name
        captured["value"] = value
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr(p, "update_secret", fake_update)

    api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="qa", env_b="dev", service="secretsmanager", name="/s",
            new_value="papitas", confirm=True,
        )
    )
    assert captured == {"name": "/s", "value": "papitas"}


def test_api_params_apply_execute_delete_forwards_to_ssm(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}
    monkeypatch.setattr(
        p, "delete_parameter",
        lambda cfg, name: captured.update(cfg_env=cfg._env, name=name) or {"ok": True, "message": "del"},
    )
    monkeypatch.setattr(
        p, "delete_secret",
        lambda cfg, name: captured.update(cfg_env=cfg._env, name=name) or {"ok": True, "message": "del"},
    )

    payload = api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="qa", env_b="dev", service="ssm", name="/x", op="delete",
            confirm=True,
        )
    )
    assert captured == {"cfg_env": "qa", "name": "/x"}
    assert payload["op"] == "delete"


def test_api_params_multi_dry_run_generates_scripts(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = []
    monkeypatch.setattr(
        p, "build_ssm_script",
        lambda name, value, value_type, cfg: captured.append(
            (name, value, value_type, cfg._env)
        )
        or f"aws ssm put-parameter --name '{name}' --type {value_type}",
    )

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/x", value='{"a": 1}', value_type="SecureString",
            envs=["dev", "qa"], dry_run=True,
        )
    )

    assert payload["dry_run"] is True
    assert payload["ok_count"] == 2
    assert len(captured) == 2
    assert set(e[3] for e in captured) == {"dev", "qa"}
    assert all("--type SecureString" in r["script"] for r in payload["results"])


def test_api_params_multi_execute_requires_confirmation(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_multi(
            CreateMultiParamsRequest(name="/x", envs=["dev"], confirm=False)
        )
    assert exc_info.value.status_code == 400
    assert "confirmación" in str(exc_info.value.detail)


def test_api_params_multi_execute_per_env_keeps_errors_isolated(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    def fake_put(cfg, name, value, value_type):
        if cfg._env == "qa":
            raise RuntimeError("boom")
        return {"ok": True, "message": "ok " + cfg._env}

    monkeypatch.setattr(p, "put_parameter", fake_put)

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/x", value="20", envs=["dev", "qa"], confirm=True
        )
    )

    by_env = {r["env"]: r for r in payload["results"]}
    assert by_env["dev"]["ok"] is True
    assert by_env["dev"]["message"] == "ok dev"
    assert by_env["qa"]["ok"] is False
    assert "boom" in by_env["qa"]["error"]
    assert payload["ok_count"] == 1
    assert payload["err_count"] == 1


def test_api_params_multi_with_secret_dry_run_builds_both_scripts(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}
    monkeypatch.setattr(
        p, "build_ssm_script",
        lambda name, value, value_type, cfg: captured.update(
            ssm=(name, value, value_type, cfg._env)
        ) or f"ssm {cfg._env}",
    )
    monkeypatch.setattr(
        p, "build_secret_script",
        lambda name, value, cfg: captured.update(
            secret=(name, value, cfg._env)
        ) or f"secret {cfg._env}",
    )

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/s", value="hunter2", create_secret=True,
            envs=["dev"], dry_run=True,
        )
    )

    assert captured["secret"] == ("/s", "hunter2", "dev")
    assert captured["ssm"][0:2] == ("/s", "/s")
    assert captured["ssm"][2] == "SecureString"
    script = payload["results"][0]["script"]
    assert "secret dev" in script and "ssm dev" in script
    assert payload["value_type"] == "SecureString"


def test_api_params_multi_ssm_secret_mode_sets_parameter_value_to_secret_name(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = {}
    monkeypatch.setattr(
        p, "build_ssm_script",
        lambda name, value, value_type, cfg: (
            captured.__setitem__("ssm", (name, value, value_type)) or f"ssm {cfg._env}"
        ),
    )
    monkeypatch.setattr(
        p, "build_secret_script",
        lambda name, value, cfg: (
            captured.__setitem__("secret", (name, value)) or f"secret {cfg._env}"
        ),
    )

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/prod/network/ecs/cluster_name",
            value="legacy-value",
            secret_name="db-pass-xd-aws",
            secret_value="12lj1242&jk4%",
            envs=["dev"],
            service="ssm",
            create_secret=True,
            dry_run=True,
            confirm=True,
        )
    )

    assert payload["results"][0]["script"]
    assert captured["ssm"] == ("/prod/network/ecs/cluster_name", "db-pass-xd-aws", "SecureString")
    assert captured["secret"] == ("db-pass-xd-aws", "12lj1242&jk4%")


def test_api_params_multi_with_secret_executes_secret_then_param(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "update_secret",
        lambda cfg, name, value: calls.append(("secret", cfg._env)) or {"ok": True, "message": "secret ok"},
    )
    monkeypatch.setattr(
        p, "put_parameter",
        lambda cfg, name, value, value_type: calls.append(("ssm", value_type, cfg._env)) or {"ok": True, "message": "ssm ok"},
    )

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/s", value="hunter2", create_secret=True,
            envs=["dev"], confirm=True,
        )
    )

    assert calls == [("secret", "dev"), ("ssm", "SecureString", "dev")]
    assert payload["ok_count"] == 1
    assert "secret ok" in payload["results"][0]["message"]


def test_api_params_multi_with_secret_partial_failure_flagged(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    monkeypatch.setattr(
        p, "update_secret",
        lambda cfg, name, value: (_ for _ in ()).throw(RuntimeError("no permission")),
    )
    monkeypatch.setattr(
        p, "put_parameter",
        lambda cfg, name, value, value_type: {"ok": True, "message": "ssm ok"},
    )

    payload = api_params_multi(
        CreateMultiParamsRequest(
            name="/s", value="hunter2", create_secret=True,
            envs=["dev"], confirm=True,
        )
    )

    row = payload["results"][0]
    assert row["ok"] is False
    assert "secreto: " in row["error"]
    assert "no permission" in row["error"]


def test_api_params_get_reads_value_and_type(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "read_parameter",
        lambda cfg, name: calls.append((cfg._env, name)) or (('{"rate": 20}', "SecureString")),
    )

    payload = api_params_get(env="dev", name="/x")
    assert calls == [("dev", "/x")]
    assert payload["value"] == '{"rate": 20}'
    assert payload["value_type"] == "SecureString"


def test_api_params_get_missing_parameter_returns_404(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    def raise_not_found(cfg, name):
        raise p.ParamNotFound(name)

    monkeypatch.setattr(p, "read_parameter", raise_not_found)

    with pytest.raises(HTTPException) as exc_info:
        api_params_get(env="dev", name="/missing")
    assert exc_info.value.status_code == 404
    assert "/missing" in str(exc_info.value.detail)


def test_api_params_multi_empty_envs_returns_400(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )

    with pytest.raises(HTTPException) as exc_info:
        api_params_multi(CreateMultiParamsRequest(name="/x", envs=[], dry_run=True))
    assert exc_info.value.status_code == 400
    assert "al menos una región" in str(exc_info.value.detail)


class _FakeConn:
    def __init__(self, env):
        self.env = env

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_connect(cfg):
    return _FakeConn(cfg._env)


def test_api_db_diff_missing_in_b_respects_include_deletes(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr("yappy_api.routes.db.connect", _fake_connect)
    monkeypatch.setattr(obj, "show_create_table",
                        lambda conn, schema, name: (
                            "CREATE TABLE ..." if conn.env == "dev" else None
                        ))
    monkeypatch.setattr(obj, "table_columns", lambda conn, schema, name: [])
    monkeypatch.setattr(obj, "table_indexes", lambda conn, schema, name: [])

    req = DbDiffRequest(
        env_a="dev", env_b="qa", schema_name="yappy", object_type="table",
        object_name="orders", include_deletes=True,
    )
    payload = api_db_diff(req)

    assert payload["status"] == "missing_in_b"
    assert payload["script"] == "DROP TABLE `yappy`.`orders`;"

    req_no_delete = DbDiffRequest(
        env_a="dev", env_b="qa", schema_name="yappy", object_type="table",
        object_name="orders",
    )
    payload_no_delete = api_db_diff(req_no_delete)

    assert payload_no_delete["status"] == "missing_in_b"
    assert payload_no_delete["script"] is None
    assert "no hay nada que sincronizar" in payload_no_delete["notes"][0]


def test_api_db_schemas_lists_user_schemas(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr("yappy_api.routes.db.connect", _fake_connect)
    monkeypatch.setattr(
        obj, "list_schemas", lambda conn: ["yappy", "yappy_payment"]
    )

    payload = api_db_schemas("dev")

    assert payload == {"env": "dev", "schemas": ["yappy", "yappy_payment"]}


def test_api_db_schemas_reports_unreachable_env(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    def _boom(cfg):
        raise SyncError("No reachable database for this environment.")

    monkeypatch.setattr("yappy_api.routes.db.connect", _boom)

    with pytest.raises(HTTPException) as exc_info:
        api_db_schemas("dev")
    assert exc_info.value.status_code == 400
    assert "No reachable database" in str(exc_info.value.detail)


def test_api_params_apply_target_b_builds_script_for_origin(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    captured = []
    monkeypatch.setattr(
        p, "build_ssm_script",
        lambda name, value, value_type, cfg: captured.append(
            (cfg._env, name, value)
        ) or f"aws ssm put-parameter --name '{name}'",
    )

    payload = api_params_apply(
        ApplyParamsRequest(
            env_a="qa", env_b="dev", service="ssm", name="/x",
            new_value="20", target="b",
        )
    )

    assert payload["script"] == "aws ssm put-parameter --name '/x'"
    assert captured == [("dev", "/x", "20")]


def test_api_params_apply_execute_target_b_forwards_put_to_origin(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "put_parameter",
        lambda cfg, name, value, value_type: calls.append(cfg._env)
        or {"ok": True, "message": "ok"},
    )

    payload = api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="qa", env_b="dev", service="ssm", op="update", name="/x",
            new_value="20", target="b", confirm=True,
        )
    )

    assert calls == ["dev"]
    assert payload["ok"] is True


def test_api_params_apply_execute_delete_ignores_target(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    calls = []
    monkeypatch.setattr(
        p, "delete_parameter",
        lambda cfg, name: calls.append(cfg._env) or {"ok": True, "message": "borrado"},
    )

    api_params_apply_execute(
        ExecuteParamsRequest(
            env_a="qa", env_b="dev", service="ssm", op="delete", name="/x",
            target="b", confirm=True,
        )
    )

    assert calls == ["qa"]


def test_api_params_apply_execute_invalid_target_rejected(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    with pytest.raises(HTTPException) as exc_info:
        api_params_apply_execute(
            ExecuteParamsRequest(
                env_a="qa", env_b="dev", service="ssm", op="update", name="/x",
                target="c", confirm=True,
            )
        )
    assert exc_info.value.status_code == 400
    assert "target" in str(exc_info.value.detail)
