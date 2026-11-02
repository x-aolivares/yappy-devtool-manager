import pytest

import yappy_cli.db.tunnel as tunnel


def test_up_keep_alive_detach_dies_before_side_effects(monkeypatch):
    called = []
    monkeypatch.setattr(tunnel, "_generate_token", lambda cfg: called.append("gen") or "tok")
    monkeypatch.setattr(tunnel, "_write_local_env", lambda token, **_: called.append("write"))
    monkeypatch.setattr(tunnel.db_cmd, "ssm_tunnel", lambda **kw: called.append("tunnel") or object())

    with pytest.raises(SystemExit):
        tunnel.up("dev", keep_alive=True, detach=True)

    assert called == [], "token generation / env write / tunnel must not run before validation"



class _LocalCfg:
    is_local = True
    db_port = 3306

    def get(self, key, default=None):
        return {"DB_PASSWORD": "root"}.get(key, default)


@pytest.mark.parametrize(
    "command, kwargs",
    [
        ("up", {"auto_refresh": False, "detach": False, "keep_alive": False}),
        ("refresh", {}),
    ],
)
def test_local_writes_env_without_token_or_tunnel(monkeypatch, command, kwargs):
    """`local` no tiene AWS: se escribe `.env.local` con su `DB_PASSWORD`."""
    called = []
    monkeypatch.setattr(tunnel.DbCommand, "validate_env", staticmethod(lambda env: None))
    monkeypatch.setattr(tunnel.Config, "with_env", staticmethod(lambda env: _LocalCfg()))
    monkeypatch.setattr(tunnel, "_generate_token", lambda cfg: called.append("gen") or "tok")
    monkeypatch.setattr(tunnel, "_write_local_env", lambda token, **_: called.append(("write", token)))
    monkeypatch.setattr(tunnel.db_cmd, "ssm_tunnel", lambda **kw: called.append("tunnel") or object())

    getattr(tunnel, command)("local", quiet_deprecation=True, **kwargs)

    assert called == [("write", "root")]
