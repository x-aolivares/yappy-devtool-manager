import pytest

import yappy_cli.db.tunnel as tunnel


def test_up_keep_alive_detach_dies_before_side_effects(monkeypatch):
    called = []
    monkeypatch.setattr(tunnel, "_generate_token", lambda cfg: called.append("gen") or "tok")
    monkeypatch.setattr(tunnel, "_write_local_env", lambda token: called.append("write"))
    monkeypatch.setattr(tunnel.db_cmd, "ssm_tunnel", lambda **kw: called.append("tunnel") or object())

    with pytest.raises(SystemExit):
        tunnel.up("dev", keep_alive=True, detach=True)

    assert called == [], "token generation / env write / tunnel must not run before validation"
