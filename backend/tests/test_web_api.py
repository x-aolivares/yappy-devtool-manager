import pytest
from fastapi import HTTPException

from yappy_api.routes.db import (
    api_compile,
    api_db_diff,
    api_db_objects,
    api_db_schemas,
    api_execute_sql,
    api_migrate,
    api_query,
)
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
    CompileRequest,
    CreateMultiParamsRequest,
    CreateSessionRequest,
    DbDiffRequest,
    ExecuteParamsRequest,
    ExecuteRequest,
    MigrationRequest,
    ParamsDiffRequest,
    QueryRequest,
    ReadParamsEntry,
    UpdateSessionItemRequest,
)
from yappy_library.adapters.database.connection import SyncError
from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import exec as syncexec
from yappy_library.application.database.sync import migrate as dbmig
from yappy_library.application.database.sync import params as p
from yappy_library.application.database.sync import query as dbquery
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

    @property
    def is_local(self):
        return self._env == "local"


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
        envs=["dev"],
        entries=[
            ReadParamsEntry(key="/rate"),
            ReadParamsEntry(key="/secret", is_secret=True),
        ],
    )

    assert payload["envs"] == ["dev"]
    assert payload["ok_count"] == 2 and payload["err_count"] == 0
    assert payload["results"][0]["key"] == "/rate"
    assert payload["results"][1]["service"] == "secretsmanager"


def test_api_params_read_reads_every_selected_environment(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa", "uat"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    seen = []

    def _read_many(cfg, entries):
        seen.append(cfg._env)
        return [
            {"env": cfg._env, "key": "/rate", "ok": True, "value": "1", "service": "ssm"}
        ]

    monkeypatch.setattr(p, "read_many", staticmethod(_read_many))

    payload = api_params_read(envs=["dev", "qa", "uat"], entries=["/rate"])

    assert seen == ["dev", "qa", "uat"]
    assert payload["envs"] == ["dev", "qa", "uat"]
    assert [r["env"] for r in payload["results"]] == ["dev", "qa", "uat"]
    assert payload["ok_count"] == 3


def test_api_params_read_keeps_other_environments_when_one_fails(monkeypatch):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: ["dev", "qa"])
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))

    def _read_many(cfg, entries):
        if cfg._env == "qa":
            raise RuntimeError("AccessDenied")
        return [{"env": "dev", "key": "/rate", "ok": True, "value": "1", "service": "ssm"}]

    monkeypatch.setattr(p, "read_many", staticmethod(_read_many))

    payload = api_params_read(envs=["dev", "qa"], entries=["/rate"])

    assert payload["ok_count"] == 1
    assert payload["err_count"] == 1
    failed = [r for r in payload["results"] if not r["ok"]]
    assert len(failed) == 1
    assert failed[0]["env"] == "qa"
    assert failed[0]["key"] == "/rate"
    assert "AccessDenied" in failed[0]["error"]


def test_api_params_read_no_environments_returns_400(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        api_params_read(envs=[], entries=["/rate"])

    assert exc_info.value.status_code == 400
    assert "al menos un ambiente" in str(exc_info.value.detail)


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
        envs=["dev"],
        entries=["/prod/ecommerce/db/master_url", "/prod/payment/stripe/secret_key"],
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
        api_params_read(envs=["dev"], entries=[])

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
        raise SyncError("No hay ninguna base de datos alcanzable para este ambiente.")

    monkeypatch.setattr("yappy_api.routes.db.connect", _boom)

    with pytest.raises(HTTPException) as exc_info:
        api_db_schemas("dev")
    assert exc_info.value.status_code == 400
    assert "base de datos alcanzable" in str(exc_info.value.detail)


# --- Compile: origen -> destino ---------------------------------------------


def _two_envs(monkeypatch, envs=("dev", "qa")):
    monkeypatch.setattr(
        Config, "known_environments", classmethod(lambda cls: list(envs))
    )
    monkeypatch.setattr(Config, "with_env", staticmethod(lambda env: _FakeConfig(env)))
    monkeypatch.setattr("yappy_api.routes.db.connect", _fake_connect)


# --- Objects: tablas / stored procedures de un schema ------------------------


def test_api_db_objects_lists_tables_and_procedures(monkeypatch):
    _two_envs(monkeypatch)
    monkeypatch.setattr(obj, "list_tables", lambda conn, schema: ["orders", "users"])
    monkeypatch.setattr(obj, "list_procedures", lambda conn, schema: ["sp_calc"])

    tables = api_db_objects(env="dev", schema="yappy", object_type="table")
    assert tables == {
        "env": "dev",
        "schema_name": "yappy",
        "object_type": "table",
        "objects": ["orders", "users"],
    }

    procedures = api_db_objects(env="dev", schema="yappy", object_type="procedure")
    assert procedures["objects"] == ["sp_calc"]


def test_api_db_objects_rejects_unknown_object_type(monkeypatch):
    _two_envs(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        api_db_objects(env="dev", schema="yappy", object_type="view")
    assert exc.value.status_code == 400
    assert "object_type" in str(exc.value.detail)


def test_api_db_objects_rejects_empty_schema(monkeypatch):
    _two_envs(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        api_db_objects(env="dev", schema="  ", object_type="table")
    assert exc.value.status_code == 400
    assert "schema" in str(exc.value.detail)


def test_api_compile_rejects_same_source_and_target(monkeypatch):
    _two_envs(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_compile(
            CompileRequest(
                env_b="dev", env_a="dev", object_type="table",
                schema_name="s", object_name="t",
            )
        )
    assert exc.value.status_code == 400


def test_api_compile_requires_schema_and_name(monkeypatch):
    _two_envs(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_compile(
            CompileRequest(
                env_b="dev", env_a="qa", object_type="table",
                schema_name="", object_name="",
            )
        )
    assert "schema" in str(exc.value.detail)


def test_api_compile_rejects_unknown_object_type(monkeypatch):
    _two_envs(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_compile(
            CompileRequest(
                env_b="dev", env_a="qa", object_type="view",
                schema_name="s", object_name="v",
            )
        )
    assert "object_type" in str(exc.value.detail)


def test_api_compile_procedure_is_replaced_from_the_source(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    monkeypatch.setattr(
        obj, "show_create_procedure",
        lambda conn, schema, name: (
            "CREATE PROCEDURE `p`() BEGIN SELECT 1; END"
            if conn.env == "dev"
            else "CREATE PROCEDURE `p`() BEGIN SELECT 2; END"
        ),
    )
    executed = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: executed.append((cfg._env, schema, code)) or [],
    )

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="procedure",
            schema_name="s", object_name="p",
        )
    )

    assert payload["status"] == "different"
    # MySQL no acepta CREATE OR REPLACE PROCEDURE: el reemplazo es DROP + CREATE.
    assert "CREATE OR REPLACE" not in payload["script"]
    assert payload["script"].startswith("DROP PROCEDURE IF EXISTS `s`.`p`;")
    assert "CREATE PROCEDURE `p`()" in payload["script"]
    assert "SELECT 1" in payload["script"]
    # El destino también se consulta, para poder mostrar qué definición hay hoy.
    assert payload["code_a"] == "CREATE PROCEDURE `p`() BEGIN SELECT 2; END"
    assert payload["code_b"] == "CREATE PROCEDURE `p`() BEGIN SELECT 1; END"
    # Compiling only generates: nothing reaches the destination yet.
    assert executed == []


def test_api_compile_never_executes_anywhere(monkeypatch):
    """Regression for the generate-only contract: ``/api/compile`` must not write.

    Every other compile test patches ``execute_sql`` to satisfy the old signature;
    this one asserts on a path that would raise loudly if the route called it, so
    a future re-introduction of the execution fails here instead of silently
    writing to a real destination.
    """
    _two_envs(monkeypatch, ("dev", "local"))
    monkeypatch.setattr(obj, "show_create_table", lambda conn, s, n: "CREATE TABLE `t` (`id` INT)")
    monkeypatch.setattr(
        obj, "table_columns",
        lambda conn, s, n: [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
                            "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
                            "COLLATION_NAME": None, "COLUMN_COMMENT": ""}],
    )
    monkeypatch.setattr(obj, "table_indexes", lambda conn, s, n: [])

    def _must_not_run(*args, **kwargs):
        raise AssertionError("/api/compile intentó ejecutar SQL: debe solo generar.")

    monkeypatch.setattr("yappy_api.routes.db.syncexec.execute_sql", _must_not_run)

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )

    assert payload["status"] == "equal"
    assert payload["script"] is None
    # The generate-only response carries no execution bookkeeping.
    assert "results" not in payload
    assert "ok_count" not in payload
    assert "err_count" not in payload


def test_api_compile_table_alters_only_the_differences(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    # The destination lacks `newcol`; the origin has it.
    monkeypatch.setattr(
        obj, "show_create_table",
        lambda conn, schema, name: (
            "CREATE TABLE `t` (`id` INT, `newcol` INT)"
            if conn.env == "dev"
            else "CREATE TABLE `t` (`id` INT)"
        ),
    )
    monkeypatch.setattr(
        obj, "table_columns",
        lambda conn, schema, name: (
            [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
              "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
              "COLLATION_NAME": None, "COLUMN_COMMENT": ""},
             {"COLUMN_NAME": "newcol", "COLUMN_TYPE": "INT", "IS_NULLABLE": "YES",
              "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
              "COLLATION_NAME": None, "COLUMN_COMMENT": ""}]
            if conn.env == "dev"
            else [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
                   "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
                   "COLLATION_NAME": None, "COLUMN_COMMENT": ""}]
        ),
    )
    monkeypatch.setattr(obj, "table_indexes", lambda conn, schema, name: [])
    executed = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: executed.append((cfg._env, code)) or [],
    )

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )

    assert payload["status"] == "different"
    assert "ADD COLUMN `newcol` INT" in payload["script"]
    assert payload["code_b"] == "CREATE TABLE `t` (`id` INT, `newcol` INT)"
    assert payload["code_a"] == "CREATE TABLE `t` (`id` INT)"
    assert executed == []


def test_api_compile_table_missing_in_destination_is_created(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    monkeypatch.setattr(
        obj, "show_create_table",
        lambda conn, s, n: "CREATE TABLE `t` (`id` INT)" if conn.env == "dev" else None,
    )

    def columns(conn, schema, name):
        if conn.env == "local":
            return []
        return [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
                 "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
                 "COLLATION_NAME": None, "COLUMN_COMMENT": ""}]

    monkeypatch.setattr(obj, "table_columns", columns)
    monkeypatch.setattr(obj, "table_indexes", lambda conn, s, n: [])
    executed = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: executed.append((cfg._env, code)) or [],
    )

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )
    assert payload["status"] == "missing_in_a"
    assert payload["script"].startswith("CREATE TABLE")
    assert payload["code_b"].startswith("CREATE TABLE")
    # El destino no tiene el objeto: no hay definición propia que ofrecer.
    assert payload["code_a"] is None
    assert executed == []


def test_api_compile_identical_table_produces_nothing(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    ddl = "CREATE TABLE `t` (`id` INT)"
    monkeypatch.setattr(obj, "show_create_table", lambda conn, s, n: ddl)
    monkeypatch.setattr(
        obj, "table_columns",
        lambda conn, s, n: [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
                            "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
                            "COLLATION_NAME": None, "COLUMN_COMMENT": ""}],
    )
    monkeypatch.setattr(obj, "table_indexes", lambda conn, s, n: [])
    executed = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: executed.append((cfg._env, code)) or [],
    )

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )
    assert payload["status"] == "equal"
    assert payload["script"] is None
    # Sin script igual hay algo que mostrar: el editor se siembra con la definición
    # del origen para que el usuario escriba arriba el cambio que quiere aplicar.
    assert payload["code_b"] == ddl
    assert payload["code_a"] == ddl
    assert executed == []


def test_api_compile_table_ddl_text_diff_but_same_structure_returns_both_codes(monkeypatch):
    """El otro caso sin script: el DDL difiere (comentario de tabla) pero columnas
    e índices son los mismos. ``script`` queda vacío y el editor se siembra igual."""
    _two_envs(monkeypatch, ("dev", "local"))
    monkeypatch.setattr(
        obj, "show_create_table",
        lambda conn, schema, name: (
            "CREATE TABLE `t` (`id` INT) COMMENT='nueva'"
            if conn.env == "dev"
            else "CREATE TABLE `t` (`id` INT) COMMENT='vieja'"
        ),
    )
    monkeypatch.setattr(
        obj, "table_columns",
        lambda conn, s, n: [{"COLUMN_NAME": "id", "COLUMN_TYPE": "INT", "IS_NULLABLE": "NO",
                            "COLUMN_DEFAULT": None, "EXTRA": "", "CHARACTER_SET_NAME": None,
                            "COLLATION_NAME": None, "COLUMN_COMMENT": ""}],
    )
    monkeypatch.setattr(obj, "table_indexes", lambda conn, s, n: [])
    executed = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: executed.append((cfg._env, code)) or [],
    )

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )
    assert payload["status"] == "different"
    assert payload["script"] == ""
    assert payload["code_b"] == "CREATE TABLE `t` (`id` INT) COMMENT='nueva'"
    assert payload["code_a"] == "CREATE TABLE `t` (`id` INT) COMMENT='vieja'"
    assert executed == []


def test_api_compile_object_missing_in_source_reports_none(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    monkeypatch.setattr(obj, "show_create_table", lambda conn, s, n: None)

    payload = api_compile(
        CompileRequest(
            env_b="dev", env_a="local", object_type="table",
            schema_name="s", object_name="t",
        )
    )
    assert payload["status"] == "none"
    assert payload["script"] is None
    # La rama `none` ni consulta el destino: la clave está, pero vacía.
    assert "code_a" in payload
    assert payload["code_a"] is None


# --- Execute: el paso que escribe -------------------------------------------


def _fake_statements(ok=True):
    return [
        syncexec.StatementResult(index=1, sql="ALTER TABLE `t` ADD `c` INT", ok=ok, ms=1.0)
    ]


def test_api_execute_sql_writes_the_text_it_is_given(monkeypatch):
    """Con origen = script el SQL no viene de un objeto con nombre propio."""
    _two_envs(monkeypatch, ("dev",))
    calls = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: calls.append((cfg._env, schema, code))
        or _fake_statements(),
    )

    payload = api_execute_sql(
        ExecuteRequest(
            env="dev", object_type="script", schema_name="", code="ALTER TABLE `t` ADD `c` INT;"
        )
    )

    # Sin schema no hay USE: el texto tiene que venir con los nombres calificados.
    assert calls == [("dev", "", "ALTER TABLE `t` ADD `c` INT;")]
    assert payload["env"] == "dev"
    # Se devuelve tal cual vino, para no etiquetar como tabla lo que no lo es.
    assert payload["object_type"] == "script"
    assert payload["ok_count"] == 1
    assert payload["err_count"] == 0


def test_api_execute_sql_uses_the_schema_as_use(monkeypatch):
    _two_envs(monkeypatch, ("dev",))
    calls = []
    monkeypatch.setattr(
        "yappy_api.routes.db.syncexec.execute_sql",
        lambda cfg, schema, code: calls.append((schema, code)) or _fake_statements(),
    )

    api_execute_sql(
        ExecuteRequest(
            env="dev", object_type="script", schema_name="yappy", code="CREATE TABLE `t` (`id` INT);"
        )
    )

    assert calls == [("yappy", "CREATE TABLE `t` (`id` INT);")]


def test_api_execute_sql_rejects_unknown_object_type(monkeypatch):
    _two_envs(monkeypatch, ("dev",))
    with pytest.raises(HTTPException) as exc:
        api_execute_sql(
            ExecuteRequest(env="dev", object_type="view", code="SELECT 1")
        )
    assert exc.value.status_code == 400
    assert "object_type" in str(exc.value.detail)


def test_api_execute_sql_rejects_empty_code(monkeypatch):
    _two_envs(monkeypatch, ("dev",))
    with pytest.raises(HTTPException) as exc:
        api_execute_sql(ExecuteRequest(env="dev", object_type="script", code="  \n -- nada\n"))
    assert "vacío" in str(exc.value.detail)


# --- Query: consultar --------------------------------------------------------


def test_api_query_returns_rows(monkeypatch):
    _two_envs(monkeypatch)
    monkeypatch.setattr(
        "yappy_api.routes.db.dbquery.run_select",
        lambda cfg, code, limit: dbquery.QueryResult(
            columns=["id"], rows=[{"id": 1}], total=1, ms=1.5
        ),
    )

    payload = api_query(QueryRequest(env="dev", code="SELECT 1"))
    assert payload["env"] == "dev"
    assert payload["columns"] == ["id"]
    assert payload["rows"] == [{"id": 1}]
    assert payload["total"] == 1


def test_api_query_rejects_a_write_statement(monkeypatch):
    _two_envs(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_query(QueryRequest(env="dev", code="DELETE FROM t"))
    assert exc.value.status_code == 400
    assert "lectura" in str(exc.value.detail)


# --- Migrate: migrar info ----------------------------------------------------


EXAMPLE_QUERY = (
    "SELECT * FROM schema_abc.table_abc abc, schema_zxc.zxc zxc "
    "WHERE zxc.abc_id = abc.abc_id AND zxc.zxc_status = 'COMPLETED' "
    "AND abc.abc_type = 'M2P' AND abc.abc_cutoff_date = '2026-10-01'"
)


def test_api_migrate_expands_the_query_into_every_table(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    captured = {}

    def fake_migrate(cfg_b, cfg_a, plan, dry_run=False):
        captured["source"] = cfg_b._env
        captured["target"] = cfg_a._env
        captured["tables"] = [t.name for t in plan.tables]
        captured["dry_run"] = dry_run
        return [
            dbmig.TableMigrationResult(
                schema="schema_abc", table="table_abc", alias="abc",
                target_schema="schema_abc", target_table="table_abc",
                select_sql="SELECT DISTINCT `abc`.* ...",
                row_count=2, replaced=0 if dry_run else 2,
            ),
            dbmig.TableMigrationResult(
                schema="schema_zxc", table="zxc", alias="zxc",
                target_schema="schema_zxc", target_table="zxc",
                select_sql="SELECT DISTINCT `zxc`.* ...",
                row_count=1, replaced=0 if dry_run else 1,
            ),
        ]

    monkeypatch.setattr("yappy_api.routes.db.dbmig.migrate", fake_migrate)

    payload = api_migrate(
        MigrationRequest(env_b="dev", env_a="local", code=EXAMPLE_QUERY, dry_run=True)
    )

    assert captured["tables"] == ["schema_abc.table_abc", "schema_zxc.zxc"]
    assert captured["source"] == "dev"
    assert captured["target"] == "local"
    assert payload["dry_run"] is True
    assert payload["ok_count"] == 2
    assert [t["table_name"] for t in payload["tables"]] == ["table_abc", "zxc"]
    assert all(t["replaced"] == 0 for t in payload["tables"])


def test_api_migrate_requires_confirmation_when_writing(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    with pytest.raises(HTTPException) as exc:
        api_migrate(
            MigrationRequest(env_b="dev", env_a="local", code=EXAMPLE_QUERY, confirm=False)
        )
    assert exc.value.status_code == 400
    assert "confirmación" in str(exc.value.detail)


def test_api_migrate_rejects_same_source_and_target(monkeypatch):
    _two_envs(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        api_migrate(
            MigrationRequest(env_b="dev", env_a="dev", code=EXAMPLE_QUERY, dry_run=True)
        )
    assert exc.value.status_code == 400


def test_api_migrate_rejects_unmigratable_queries(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    with pytest.raises(HTTPException) as exc:
        api_migrate(
            MigrationRequest(
                env_b="dev", env_a="local",
                code="SELECT id FROM t GROUP BY id", dry_run=True,
            )
        )
    assert exc.value.status_code == 400
    assert "GROUP BY" in str(exc.value.detail)


def test_api_migrate_defaults_the_schema_for_unqualified_tables(monkeypatch):
    _two_envs(monkeypatch, ("dev", "local"))
    captured = {}

    def fake_migrate(cfg_b, cfg_a, plan, dry_run=False):
        captured["tables"] = [t.name for t in plan.tables]
        return []

    monkeypatch.setattr("yappy_api.routes.db.dbmig.migrate", fake_migrate)
    api_migrate(
        MigrationRequest(
            env_b="dev", env_a="local", code="SELECT * FROM orders",
            default_schema="shop", dry_run=True,
        )
    )
    assert captured["tables"] == ["shop.orders"]


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
