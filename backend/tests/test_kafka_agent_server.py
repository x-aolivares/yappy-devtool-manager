"""El contrato HTTP del proceso del agente, tal como lo consume un cliente externo.

Los tests de `test_kafka_agent.py` cubren la API principal y las rutas del agente;
acá se exercise el proceso en el puerto 9091 con un `TestClient`, porque es lo que
usa un script o un Postman que apunte directo al agente. Es la mitad del contrato
que no se podía perder al portar el repo original.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from yappy_api.kafka_agent_server import app

client = TestClient(app)

_ENVELOPE = {"data": {"key": "k", "value": {"foo": "bar"}, "headers": {"ce_id": "evt-1"}}}


def _patch(monkeypatch, **methods):
    """Sustituye métodos del singleton del agente para no necesitar un broker."""
    from yappy_api import kafka_agent_server

    for name, value in methods.items():
        monkeypatch.setattr(kafka_agent_server.kafka_agent.agent, name, value)


def test_health_reports_the_process_and_its_brokers(monkeypatch):
    _patch(monkeypatch, servers=lambda: ["127.0.0.1:9092"])

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "port": 9091, "brokers": ["127.0.0.1:9092"]}


def test_producer_returns_200_with_the_original_body(monkeypatch):
    sent: list[dict[str, Any]] = []

    def publish(**kwargs):
        sent.append(kwargs)

    _patch(monkeypatch, publish=publish)
    body = {
        "topic": "topic-a",
        "cloud_event_id": "evt-1",
        "type": "com.evento",
        "headers": {"customheader": "valor1"},
        "message": {"foo": "bar"},
    }

    response = client.post("/producer", json=body)

    assert response.status_code == 200
    assert response.json() == {
        "status": {"code": 200, "message": "Publicacion exitosa"},
        "data": body,
    }
    assert sent[0]["topic"] == "topic-a"
    assert sent[0]["cloud_event_id"] == "evt-1"


def test_producer_accepts_payload_as_an_alias(monkeypatch):
    seen: list[dict[str, Any]] = []
    _patch(monkeypatch, publish=lambda **kwargs: seen.append(kwargs))

    response = client.post("/producer", json={"topic": "t", "payload": {"a": 1}})

    assert response.status_code == 200
    assert seen[0]["message"] == {"a": 1}


def test_producer_reports_500_when_the_broker_fails(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("broker caído")

    _patch(monkeypatch, publish=broken)

    failed = client.post("/producer", json={"topic": "t", "message": {"a": 1}})

    assert failed.status_code == 500
    assert failed.json()["status"]["message"] == "Error al publicar"


@pytest.mark.parametrize("path", ["/producer", "/consumer", "/v2/consumer"])
def test_the_read_and_write_endpoints_refuse_a_get(path):
    assert client.get(path).status_code == 400


def test_consumer_v1_returns_the_envelope_and_the_elapsed_time(monkeypatch):
    _patch(monkeypatch, find_by_cloud_event_id=lambda *a: dict(_ENVELOPE))

    response = client.post("/consumer", json={"cloud_event_id": "evt-1", "topic": "topic-a"})

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == _ENVELOPE["data"]
    assert body["response_time_ms"] >= 0


def test_consumer_v2_returns_a_list_of_matches(monkeypatch):
    seen: list[tuple] = []

    def find(key, value, topic, max_polls):
        seen.append((key, value, topic, max_polls))
        return [_ENVELOPE]

    _patch(monkeypatch, find_by_header=find)

    response = client.post("/v2/consumer", json={
        "header_key": "customheader", "header_value": "valor1",
        "topic": "topic-a", "max_polls": 5,
    })

    assert response.status_code == 200
    assert response.json()["data"] == [_ENVELOPE]
    assert seen == [("customheader", "valor1", "topic-a", 5)]


@pytest.mark.parametrize("path", ["/consumer", "/v2/consumer"])
def test_a_miss_returns_404_with_the_elapsed_time(monkeypatch, path):
    _patch(monkeypatch, find_by_cloud_event_id=lambda *a: None, find_by_header=lambda *a: None)

    response = client.post(path, json={"topic": "topic-a"})

    assert response.status_code == 404
    assert response.json()["data"] is None
    assert response.json()["response_time_ms"] >= 0


def test_listener_validates_the_topic_before_touching_kafka(monkeypatch):
    calls: list[str] = []
    _patch(monkeypatch, listen=lambda topic, max_polls: (calls.append(topic), dict(_ENVELOPE))[1])

    missing = client.post("/listener", json={})
    found = client.post("/listener", json={"topic": "topic-a", "max_polls": 2})

    assert missing.status_code == 400
    assert missing.json()["status"]["message"] == "El campo 'topic' es requerido"
    assert found.status_code == 200
    assert calls == ["topic-a"]


def test_listener_returns_404_when_nothing_is_there(monkeypatch):
    _patch(monkeypatch, listen=lambda *a: None)

    assert client.post("/listener", json={"topic": "topic-a"}).status_code == 404


def test_clean_topics_uses_the_wrapped_body_of_the_original(monkeypatch):
    seen: list[list[str]] = []

    def delete(topics):
        seen.append(topics or [])
        return {"status": {"code": 200, "message": "Tópicos eliminados"}, "data": {"topics_deleted": topics}}

    _patch(monkeypatch, delete_topics=delete)

    response = client.post("/clean-topics", json={"body": {"topics": ["topic-a"]}})

    assert response.status_code == 200
    assert seen == [["topic-a"]]


@pytest.mark.parametrize("topics", [
    "topic-a",
    [1, 2],
])
def test_clean_topics_rejects_a_malformed_list(topics):
    response = client.post("/clean-topics", json={"body": {"topics": topics}})

    assert response.status_code == 400
    assert response.json()["status"]["code"] == 400


def test_clean_topics_keeps_the_agent_status_code(monkeypatch):
    def delete(topics):
        return {"status": {"code": 207, "message": "Faltó un tópico"}, "data": {}}

    _patch(monkeypatch, delete_topics=delete)

    response = client.post("/clean-topics", json={"body": {"topics": ["topic-a"]}})

    assert response.status_code == 207
