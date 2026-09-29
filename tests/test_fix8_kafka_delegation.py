import subprocess
from types import SimpleNamespace

import pytest

import library.api.kafka as api_kafka
import library.kafka.manager as manager
import cli.workflow.debug as debug


def _sentinel(*args, **kwargs):
    raise AssertionError("private subprocess call made")


class _FakeKafkaService:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def up(self, target, detach=False):
        self.calls.append(("up", target, detach))

    def down(self, target):
        self.calls.append(("down", target))


def test_manager_up_server_delegates_to_service(monkeypatch):
    fake = _FakeKafkaService()
    monkeypatch.setattr(manager, "KafkaService", lambda *a, **k: fake)
    monkeypatch.setattr(subprocess, "Popen", _sentinel)
    monkeypatch.setattr(subprocess, "run", _sentinel)

    manager.up("server", detach=False, quiet_deprecation=True)

    assert fake.calls == [("up", "server", False)]


def test_manager_up_ui_delegates_to_service(monkeypatch):
    fake = _FakeKafkaService()
    monkeypatch.setattr(manager, "KafkaService", lambda *a, **k: fake)
    monkeypatch.setattr(subprocess, "Popen", _sentinel)
    monkeypatch.setattr(subprocess, "run", _sentinel)

    manager.up("ui", detach=True, quiet_deprecation=True)

    assert fake.calls == [("up", "ui", True)]


def test_manager_up_clean_delegates_to_service(monkeypatch):
    fake = _FakeKafkaService()
    monkeypatch.setattr(manager, "KafkaService", lambda *a, **k: fake)
    monkeypatch.setattr(subprocess, "Popen", _sentinel)
    monkeypatch.setattr(subprocess, "run", _sentinel)

    manager.up("clean", detach=False, quiet_deprecation=True)

    assert fake.calls == [("up", "clean", False)]


def test_manager_down_delegates_to_service(monkeypatch):
    fake = _FakeKafkaService()
    monkeypatch.setattr(manager, "KafkaService", lambda *a, **k: fake)
    monkeypatch.setattr(subprocess, "Popen", _sentinel)
    monkeypatch.setattr(subprocess, "run", _sentinel)

    manager.down("ui", quiet_deprecation=True)

    assert fake.calls == [("down", "ui")]


@pytest.mark.parametrize("call", ["up", "down", "clean"])
def test_kafka_service_rejects_non_windows(call, monkeypatch):
    monkeypatch.setattr(api_kafka.sys, "platform", "linux")
    messages = []

    def fake_die(msg, code=1):
        messages.append(msg)
        raise SystemExit(code)

    monkeypatch.setattr(api_kafka, "die", fake_die)
    svc = api_kafka.KafkaService(SimpleNamespace(
        kafka_core_path="C:\\kafka\\kafka-core",
        kafka_ui_path="C:\\kafka\\kafka-ui",
        kafka_path="C:\\kafka",
    ))
    with pytest.raises(SystemExit):
        if call == "up":
            svc.up("server")
        elif call == "down":
            svc.down("server")
        else:
            svc.clean()
    assert messages == ["Kafka management currently supports Windows only"]


def _alive_proc():
    return SimpleNamespace(pid=1, poll=lambda: None, terminate=lambda: None, returncode=None)


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


def test_debug_local_kafka_steps_delegate_to_service(monkeypatch):
    calls = []

    def fake_up(target, detach=False):
        calls.append(("up", target, detach))
        return None

    fake_service = SimpleNamespace(up=fake_up, cleanup=lambda: calls.append(("cleanup",)))
    monkeypatch.setattr(debug, "KafkaService", lambda cfg: fake_service)
    monkeypatch.setattr(debug.wf_cmd, "_check_session", lambda: True)
    monkeypatch.setattr(debug.wf_cmd, "check_requirements", lambda *a: None)
    monkeypatch.setattr(debug.wf_cmd, "ssm_tunnel", lambda **kw: _alive_proc())
    monkeypatch.setattr(debug.wf_cmd, "kill_ssm", lambda: None)
    monkeypatch.setattr(debug, "_generate_token", lambda cfg: "tok")
    monkeypatch.setattr(debug, "_write_local_env", lambda token: None)
    monkeypatch.setattr(debug.Config, "with_env", staticmethod(lambda env: _fake_cfg()))
    monkeypatch.setattr(debug.time, "sleep", _interrupt_on_loop_sleep)
    monkeypatch.setattr(subprocess, "Popen", _sentinel)
    monkeypatch.setattr(subprocess, "run", _sentinel)

    debug.debug_local("dev", kafka_agents_path=None, quiet_deprecation=True)

    assert ("up", "server", True) in calls
    assert ("up", "ui", True) in calls
    assert ("cleanup",) in calls


def _interrupt_on_loop_sleep(seconds):
    if seconds == 1:
        raise KeyboardInterrupt()
