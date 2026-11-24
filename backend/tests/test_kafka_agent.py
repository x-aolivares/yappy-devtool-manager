"""El Kafka Agent integrado con la web: contrato, operaciones y prueba local."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kafka.structs import TopicPartition

from yappy_api.routes import kafka_agent as routes
from yappy_library.adapters.kafka import agent as agent_module
from yappy_library.adapters.kafka.agent import KafkaAgent, cloud_event_record
from yappy_library.adapters.kafka.agent_smoke import run_smoke


class _Future:
    def get(self, timeout: float) -> None:
        return None


class _Producer:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, topic: str, value: bytes, headers: list[tuple[str, bytes]]):
        self.sent.append({"topic": topic, "value": value, "headers": headers})
        return _Future()

    def close(self) -> None:
        return None


class _Consumer:
    def __init__(self, messages: list[Any] | None = None) -> None:
        self.messages = messages or []
        self.assigned: list[Any] = []
        self.seeks: list[tuple[Any, int]] = []
        self.closed = False

    def topics(self) -> set[str]:
        return {"topic-a", "topic-b", "__consumer_offsets"}

    def partitions_for_topic(self, topic: str) -> set[int] | None:
        return {0} if topic in {"topic-a", "topic-b"} else None

    def end_offsets(self, partitions: list[Any]) -> dict[Any, int]:
        return {partition: len(self.messages) for partition in partitions}

    def assign(self, partitions: list[Any]) -> None:
        self.assigned = partitions

    def seek(self, partition: Any, offset: int) -> None:
        self.seeks.append((partition, offset))

    def poll(self, timeout_ms: int) -> dict[Any, list[Any]]:
        if not self.messages:
            return {}
        first = self.messages[0]
        self.messages = []
        return {TopicPartition("topic-a", 0): [first]}

    def close(self, autocommit: bool = True) -> None:
        self.closed = True


class _Admin:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_topics(self, topics: list[str], timeout_ms: int, raise_errors: bool):
        self.deleted = topics
        return {"topics": [{"name": topic, "error_code": 0} for topic in topics]}

    def close(self) -> None:
        return None


class _Cfg:
    def __init__(self, bootstrap: str = "", core: str = "") -> None:
        self.values = {"KAFKA_BOOTSTRAP_SERVERS": bootstrap}
        self.kafka_core_path = core

    def get(self, key: str) -> str:
        return self.values.get(key, "")


def _message(ce_id: str, value: str = '{"source": "test"}') -> SimpleNamespace:
    return SimpleNamespace(
        key=b"key",
        value=value.encode("utf-8"),
        headers=[("ce_id", ce_id.encode("utf-8"))],
    )


def _agent(producer: _Producer, consumer: _Consumer, admin: _Admin) -> KafkaAgent:
    return KafkaAgent(
        producer_factory=lambda servers: producer,
        consumer_factory=lambda servers: consumer,
        admin_factory=lambda servers: admin,
        config_factory=lambda: _Cfg("127.0.0.1:9092"),
    )


# --- el contrato del agente --------------------------------------------------


def test_bootstrap_uses_explicit_servers_before_the_local_properties():
    cfg = _Cfg("host:1234,otro:9092")

    assert agent_module.bootstrap_servers(cfg) == ["host:1234", "otro:9092"]


def test_bootstrap_falls_back_to_ipv4_and_the_broker_port(tmp_path):
    config_dir = tmp_path / "config" / "kraft"
    config_dir.mkdir(parents=True)
    (config_dir / "server.properties").write_text(
        "listeners=PLAINTEXT://:19092,CONTROLLER://:9093\n", encoding="utf-8",
    )

    assert agent_module.bootstrap_servers(_Cfg(core=str(tmp_path))) == ["127.0.0.1:19092"]


def test_cloud_event_headers_match_the_original_binary_mode():
    headers, value = cloud_event_record(
        "evt-1", "com.evento", {"customheader": "valor", "datacontenttype": "texto"},
        {"foo": "bar"},
    )
    as_dict = dict(headers)

    assert as_dict["content-type"] == b"application/json"
    assert as_dict["ce_id"] == b"evt-1"
    assert as_dict["ce_type"] == b"com.evento"
    assert as_dict["ce_customheader"] == b"valor"
    assert as_dict["ce_source"] == b"source"
    assert as_dict["ce_specversion"] == b"1.0"
    assert json.loads(value) == {"foo": "bar"}


def test_publish_waits_for_the_broker_ack():
    producer = _Producer()
    _agent(producer, _Consumer(), _Admin()).publish(
        "topic-a", "evt-1", {"foo": "bar"}, {}, "com.evento",
    )

    assert producer.sent[0]["topic"] == "topic-a"
    assert dict(producer.sent[0]["headers"])["ce_id"] == b"evt-1"


def test_consumer_v1_finds_by_ce_id_and_v2_by_any_header():
    producer, admin = _Producer(), _Admin()
    for ce_id in ("evt-1", "evt-2"):
        saga = _agent(producer, _Consumer([_message(ce_id)]), admin)
        found = saga.find_by_cloud_event_id(ce_id, "topic-a", 2)

        assert found is not None
        assert found["data"]["headers"]["ce_id"] == ce_id
        assert found["data"]["value"] == {"source": "test"}

    saga = _agent(producer, _Consumer([_message("evt-3")]), admin)
    assert saga.find_by_header("ce_id", "evt-3", "topic-a", 2) is not None
    assert saga.find_by_header("ce_id", "otro", "topic-a", 2) is None


def test_listener_returns_the_first_recent_message():
    saga = _agent(_Producer(), _Consumer([_message("evt-1")]), _Admin())

    found = saga.listen("topic-a", 2)

    assert found is not None
    assert found["data"]["headers"]["ce_id"] == "evt-1"


def test_delete_topics_skips_internal_topics_and_reports_missing_ones():
    admin = _Admin()
    saga = _agent(_Producer(), _Consumer(), admin)

    result = saga.delete_topics(["topic-a", "no-existe"])

    assert admin.deleted == ["topic-a"]
    assert result["status"]["code"] == 207
    assert result["data"]["topics_deleted"] == ["topic-a"]
    assert result["data"]["topics_not_found"] == ["no-existe"]


# --- la prueba local ---------------------------------------------------------


def _fake_post(calls: list[tuple[str, dict[str, Any]]]):
    def post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        calls.append((path, body))
        ce_id = body.get("cloud_event_id") or body.get("header_value")
        if path == "/producer":
            return 200, {"status": {"code": 200}}
        if path == "/listener":
            return 200, {"data": {"key": "", "value": {}, "headers": {"ce_id": ce_id}}}
        return 200, {"data": [{"data": {"headers": {"ce_id": ce_id}, "value": {}, "key": ""}}]}

    return post


def test_smoke_exercises_all_three_read_paths_and_can_clean_up():
    calls: list[tuple[str, dict[str, Any]]] = []

    report = run_smoke(_fake_post(calls), topic="topic-a", messages=2, max_polls=3, cleanup=True)

    assert report["ok"] is True
    assert [name for name, _ in calls] == [
        "/producer", "/producer", "/consumer", "/v2/consumer", "/listener", "/clean-topics",
    ]
    assert {check["name"] for check in report["checks"]} == {
        "producer", "producer-2", "consumer", "consumer_v2", "listener", "cleanup",
    }


def test_smoke_reports_which_step_failed():
    def failing(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return (404, {"data": None}) if path == "/consumer" else (200, {"data": {}})

    report = run_smoke(failing)

    assert report["ok"] is False
    consumer = next(check for check in report["checks"] if check["name"] == "consumer")
    assert consumer["status"] == 404
    assert consumer["ok"] is False


# --- los endpoints de la API principal ---------------------------------------


def test_status_reports_a_down_agent_without_raising(monkeypatch):
    monkeypatch.setattr(routes, "_request", lambda *a, **k: (409, routes._NOT_RUNNING))

    response = asyncio.run(routes.api_kafka_agent_status())

    assert response.status_code == 200
    assert json.loads(response.body)["ok"] is False


def test_status_forwards_the_agent_health(monkeypatch):
    health = {"ok": True, "port": 9091, "brokers": ["127.0.0.1:9092"]}
    monkeypatch.setattr(routes, "_request", lambda *a, **k: (200, health))

    response = asyncio.run(routes.api_kafka_agent_status())

    assert json.loads(response.body) == health


def test_the_five_operations_keep_the_agent_status_code(monkeypatch):
    seen: list[str] = []

    def fake(path: str, body: Any = None, method: str = "POST") -> tuple[int, Any]:
        seen.append(path)
        return 404, {"data": None, "response_time_ms": 1.0}

    monkeypatch.setattr(routes, "_request", fake)
    body: dict[str, Any] = {"topic": "topic-a"}

    producer = asyncio.run(routes.api_kafka_agent_producer(body))
    consumer = asyncio.run(routes.api_kafka_agent_consumer(body))
    v2 = asyncio.run(routes.api_kafka_agent_consumer_v2(body))
    listener = asyncio.run(routes.api_kafka_agent_listener(body))
    clean = asyncio.run(routes.api_kafka_agent_clean_topics(body))

    assert seen == [
        "/producer", "/consumer", "/v2/consumer", "/listener", "/clean-topics",
    ]
    assert [response.status_code for response in (producer, consumer, v2, listener, clean)] == [404] * 5


def test_the_test_endpoint_refuses_when_the_agent_is_down(monkeypatch):
    monkeypatch.setattr(routes, "_request", lambda *a, **k: (409, routes._NOT_RUNNING))
    request = routes.KafkaAgentSmokeRequest(topic="topic-a")

    response = asyncio.run(routes.api_kafka_agent_run_tests(request))

    assert response.status_code == 409


def test_the_test_endpoint_returns_the_report(monkeypatch):
    report = {
        "ok": True, "topic": "topic-a", "correlation_id": "evt-1", "messages": 1,
        "checks": [], "total_ms": 1.0,
    }
    monkeypatch.setattr(routes, "_request", lambda *a, **k: (200, {"ok": True}))
    monkeypatch.setattr(routes, "run_smoke", lambda *a, **k: report)
    request = routes.KafkaAgentSmokeRequest(topic="topic-a")

    response = asyncio.run(routes.api_kafka_agent_run_tests(request))

    assert response.status_code == 200
    assert json.loads(response.body) == report


@pytest.mark.parametrize("path", [
    "/producer", "/consumer", "/v2/consumer", "/listener", "/clean-topics",
])
def test_every_agent_operation_is_in_the_openapi_schema(path):
    from yappy_api.app import app

    spec = app.openapi()
    assert f"/api/kafka-agent{path}" in spec["paths"]
