from types import SimpleNamespace

import yappy_cli.workflow.debug as debug


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


def test_debug_local_generates_and_writes_token_before_tunnel(monkeypatch):
    order = []

    monkeypatch.setattr(debug.wf_cmd, "_check_session", lambda: True)
    monkeypatch.setattr(debug.wf_cmd, "check_requirements", lambda *a: None)
    monkeypatch.setattr(debug.wf_cmd, "kill_ssm", lambda: None)
    monkeypatch.setattr(debug.Config, "with_env", staticmethod(lambda env: _fake_cfg()))
    monkeypatch.setattr(
        debug, "_generate_token",
        lambda cfg: order.append("token") or "tok",
    )
    monkeypatch.setattr(
        debug, "_write_local_env",
        lambda token: order.append("write_env"),
    )
    monkeypatch.setattr(
        debug.wf_cmd, "ssm_tunnel",
        lambda **kw: order.append("tunnel") or _alive_proc(),
    )
    monkeypatch.setattr(
        debug, "KafkaService",
        lambda cfg: SimpleNamespace(up=lambda target, detach=False: None, cleanup=lambda: None),
    )
    monkeypatch.setattr(debug.time, "sleep", _sleep_then_interrupt)

    debug.debug_local("dev", kafka_agents_path=None, quiet_deprecation=True)

    assert order[:3] == ["token", "write_env", "tunnel"], (
        "token generation and env write must happen before the tunnel starts"
    )


def _alive_proc():
    return SimpleNamespace(pid=1, poll=lambda: None, terminate=lambda: None, returncode=None)


def _sleep_then_interrupt(seconds):
    if seconds == 1:
        raise KeyboardInterrupt()
