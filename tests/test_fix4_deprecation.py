from types import SimpleNamespace

import pytest

import yappy_cli.db.tunnel as tunnel
import yappy_cli.ssm.tunnel as ssm_tunnel
import yappy_cli.verbs.run as run_verbs


def _fake_cfg():
    required = {
        "AWS_INSTANCE": "i-123",
        "AWS_PORT": "53360",
        "AWS_REGION": "us-east-1",
        "AWS_HOST": "db.example.com",
        "AWS_USER": "app",
    }
    return SimpleNamespace(
        profile="dev",
        db_port=8100,
        get=lambda key, default=None: {"AWS_PORT": "53360"}.get(key, default),
        require=lambda key: required[key],
    )


def _db_mocks(monkeypatch):
    monkeypatch.setattr(tunnel.Config, "with_env", staticmethod(lambda env: _fake_cfg()))
    monkeypatch.setattr(tunnel, "_generate_token", lambda cfg: "tok")
    monkeypatch.setattr(tunnel, "_write_local_env", lambda token: None)
    monkeypatch.setattr(tunnel.db_cmd, "check_requirements", lambda *a: None)
    monkeypatch.setattr(
        tunnel.db_cmd,
        "ssm_tunnel",
        lambda **kw: SimpleNamespace(pid=1, poll=lambda: None, terminate=lambda: None, wait=lambda timeout=0: None, returncode=0),
    )
    monkeypatch.setattr(tunnel.db_cmd, "serve", lambda *a, **k: None)


def test_run_db_no_deprecation_warning(monkeypatch, capsys):
    _db_mocks(monkeypatch)

    run_verbs.run_db(env="dev", detach=False, keep_alive=False, auto_refresh=False)

    out = capsys.readouterr().out
    assert "DEPRECATED" not in out


def test_old_db_up_still_warns(monkeypatch, capsys):
    _db_mocks(monkeypatch)

    tunnel.up("dev", auto_refresh=False, detach=False, keep_alive=False)

    out = capsys.readouterr().out
    assert "DEPRECATED" in out


def test_ssm_producer_hint_points_to_verb(monkeypatch, capsys):
    monkeypatch.setattr(ssm_tunnel.Config, "with_env", staticmethod(lambda env: _fake_cfg()))
    monkeypatch.setattr(ssm_tunnel.ssm_cmd, "check_requirements", lambda *a: None)
    monkeypatch.setattr(ssm_tunnel.ssm_cmd, "ssm_tunnel", lambda **kw: SimpleNamespace(pid=1))
    monkeypatch.setattr(ssm_tunnel.ssm_cmd, "serve", lambda *a, **k: None)

    ssm_tunnel.producer(env="dev", detach=False)

    out = capsys.readouterr().out
    assert "tunnel producer <env>" in out


def test_deprecation_hints_dict_fixed():
    from yappy_cli.deprecation import _OLD_TO_NEW

    assert _OLD_TO_NEW["ssm producer"] == "run tunnel producer <env>"
    assert _OLD_TO_NEW["workflow executor"] == "run workflow <env>"
