"""Pruebas locales del Kafka Agent, en Python puro.

Donde el repo original usaba JMeter (Docker) y Karate (Java), acá se ejercitan
los cinco endpoints con ``urllib`` contra el proceso del agente. No reemplaza a
las suites de carga —para eso siguen siendo JMeter y Karate— pero sí contesta a
la pregunta que se hace uno antes de cualquier prueba: ¿el agente publica,
encuentra el mensaje por los dos caminos y escucha el tópico?

Cada check se mide por separado y se devuelve aunque falle, para que el panel
diga *qué* paso no funcionó en vez de un ``500`` sin detalle.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from yappy_library.adapters.kafka.agent import CE_ID_HEADER, DEFAULT_MAX_POLLS

#: Los corps de prueba van a este tópico por defecto. No es `topic-a`: el pipeline
#: del repo original escucha ahí y una prueba no debería inyectarle mensajes.
DEFAULT_TOPIC = "yappy-agent-smoke"

Post = Callable[[str, dict[str, Any]], tuple[int, Any]]


def _check(
    checks: list[dict[str, Any]], name: str, status: int | None,
    ok: bool, detail: str, elapsed_ms: float,
) -> None:
    checks.append(
        {
            "name": name,
            "ok": ok,
            "status": status,
            "detail": detail,
            "elapsed_ms": elapsed_ms,
        }
    )


def run_smoke(
    post: Post,
    topic: str = DEFAULT_TOPIC,
    messages: int = 1,
    max_polls: int = DEFAULT_MAX_POLLS,
    cleanup: bool = False,
) -> dict[str, Any]:
    """Publica, busca por v1 y v2, escucha y (opcionalmente) limpia el tópico.

    ``post`` es el mismo reenvío que usa la API principal; inyectarlo acá permite
    probar la secuencia completa sin levantar un broker ni un proceso.
    """
    started = time.time()
    checks: list[dict[str, Any]] = []
    correlation_id = f"yappy-smoke-{uuid.uuid4().hex}"
    payload = {"source": "yappy-web", "timestamp": int(time.time() * 1000)}

    step = time.time()
    try:
        status, _ = post("/producer", {
            "topic": topic,
            "cloud_event_id": correlation_id,
            "type": "yappy.smoke",
            "message": payload,
            "headers": {},
        })
        _check(checks, "producer", status, status == 200, "Publicación del evento de prueba",
               round((time.time() - step) * 1000, 2))
    except Exception as exc:  # noqa: BLE001 - el agente caído es un resultado, no un 500
        status = None
        _check(checks, "producer", None, False, str(exc), round((time.time() - step) * 1000, 2))

    # Con `messages > 1`, el smoke también comprueba que publicar en ráfaga no
    # rompe la búsqueda: el último id es el que tiene que aparecer.
    last_id = correlation_id
    for index in range(max(0, messages - 1)):
        extra_id = f"{correlation_id}-{index + 1}"
        step = time.time()
        try:
            status, _ = post("/producer", {
                "topic": topic,
                "cloud_event_id": extra_id,
                "type": "yappy.smoke",
                "message": {"source": "yappy-web", "sequence": index + 1},
                "headers": {},
            })
            _check(checks, f"producer-{index + 2}", status, status == 200,
                   f"Mensaje {index + 2} de {messages}", round((time.time() - step) * 1000, 2))
            last_id = extra_id
        except Exception as exc:  # noqa: BLE001
            _check(checks, f"producer-{index + 2}", None, False, str(exc),
                   round((time.time() - step) * 1000, 2))

    def _verify(name: str, path: str, body: dict[str, Any], expected_id: str) -> None:
        step = time.time()
        try:
            status, result = post(path, body)
            data = (result or {}).get("data")
            items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            found = any(
                isinstance(item, dict)
                and isinstance(item.get("data"), dict)
                and item["data"].get("headers", {}).get(CE_ID_HEADER) == expected_id
                for item in items
            )
            ok = status == 200 and found
            detail = (
                "Mensaje encontrado" if ok
                else "El mensaje publicado no apareció en el tópico"
            )
            _check(checks, name, status, ok, detail, round((time.time() - step) * 1000, 2))
        except Exception as exc:  # noqa: BLE001
            _check(checks, name, None, False, str(exc), round((time.time() - step) * 1000, 2))

    _verify("consumer", "/consumer", {
        "cloud_event_id": correlation_id, "topic": topic, "max_polls": max_polls,
    }, correlation_id)
    _verify("consumer_v2", "/v2/consumer", {
        "header_key": CE_ID_HEADER, "header_value": last_id,
        "topic": topic, "max_polls": max_polls,
    }, last_id)

    step = time.time()
    try:
        status, result = post("/listener", {"topic": topic, "max_polls": max_polls})
        ok = status == 200 and isinstance((result or {}).get("data"), dict)
        _check(checks, "listener", status, ok,
               "Primer mensaje reciente del tópico" if ok else "No se pudo escuchar el tópico",
               round((time.time() - step) * 1000, 2))
    except Exception as exc:  # noqa: BLE001
        _check(checks, "listener", None, False, str(exc), round((time.time() - step) * 1000, 2))

    if cleanup:
        step = time.time()
        try:
            status, _ = post("/clean-topics", {"body": {"topics": [topic]}})
            _check(checks, "cleanup", status, status in (200, 207),
                   f"Se borró {topic}" if status in (200, 207) else "No se pudo borrar el tópico",
                   round((time.time() - step) * 1000, 2))
        except Exception as exc:  # noqa: BLE001
            _check(checks, "cleanup", None, False, str(exc), round((time.time() - step) * 1000, 2))

    return {
        "ok": all(check["ok"] for check in checks),
        "topic": topic,
        "correlation_id": correlation_id,
        "messages": max(1, messages),
        "checks": checks,
        "total_ms": round((time.time() - started) * 1000, 2),
    }
