"""Kafka Agent en la API principal: el proceso del agente, visto desde ``/docs``.

Las rutas reales viven en el proceso del agente (``yappy_api.kafka_agent_server``,
puerto 9091) con el contrato original de ``kafka-agents``. Estas cuatro rutas las
repiten bajo ``/api/kafka-agent`` para que aparezcan en ``/docs`` y en
``openapi.json`` junto al resto de la API. Reenvían el body tal cual y devuelven
la respuesta del agente con su mismo status code.

Si el agente no está levantado, la respuesta es 409. Si está levantado pero no
contesta a tiempo, 503.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from yappy_library.adapters.kafka.service import KAFKA_AGENT_PORT

router = APIRouter(tags=["kafka-agent"])

_PREFIX = "/api/kafka-agent"

#: Una búsqueda puede tardar lo que tarda el agente en recorrer el tópico (hasta ~10s).
_TIMEOUT_SECONDS = 60

_NOT_RUNNING = {
    "status": {
        "code": 409,
        "message": "El Kafka Agent no está levantado. Levantalo desde la sección Kafka local.",
    },
    "data": None,
}


def _forward(path: str, body: Any) -> tuple[int, Any]:
    """POST ``body`` al agente en ``path`` y devolver ``(status, json)``."""
    url = f"http://127.0.0.1:{KAFKA_AGENT_PORT}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return 409, _NOT_RUNNING
        return 503, {"status": {"code": 503, "message": f"El Kafka Agent no respondió: {exc.reason}"}, "data": None}
    except TimeoutError:
        return 503, {"status": {"code": 503, "message": "El Kafka Agent no respondió a tiempo"}, "data": None}


def _response(status: int, content: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content=content)


@router.post(
    f"{_PREFIX}/producer",
    operation_id="kafka_agent_producer",
    summary="Publicar un CloudEvent en un tópico",
    description="Reenvía a `POST /producer` del agente. `message` (o su alias `payload`) es el cuerpo del evento.",
)
async def api_kafka_agent_producer(
    body: Annotated[
        dict[str, Any],
        Body(
            openapi_examples={
                "evento": {
                    "summary": "Evento con header propio",
                    "value": {
                        "topic": "mi-topico",
                        "cloud_event_id": "evt-001",
                        "type": "com.ejemplo.evento",
                        "headers": {"customheader": "valor1"},
                        "message": {"key": "value", "foo": "bar"},
                    },
                }
            }
        ),
    ],
):
    status, content = await run_in_threadpool(_forward, "/producer", body)
    return _response(status, content)


@router.post(
    f"{_PREFIX}/consumer",
    operation_id="kafka_agent_consumer",
    summary="Buscar mensajes por header",
    description=(
        "Reenvía a `POST /consumer` del agente. Para buscar por `ce_id`, usá "
        "`header_key: ce_id`."
    ),
)
async def api_kafka_agent_consumer(
    body: Annotated[
        dict[str, Any],
        Body(
            openapi_examples={
                "por-ce-id": {
                    "summary": "Buscar por cloud event id",
                    "value": {
                        "header_key": "ce_id",
                        "header_value": "evt-001",
                        "topic": "mi-topico",
                        "max_polls": 3,
                    },
                }
            }
        ),
    ],
):
    status, content = await run_in_threadpool(_forward, "/consumer", body)
    return _response(status, content)


@router.post(
    f"{_PREFIX}/listener",
    operation_id="kafka_agent_listener",
    summary="Primer mensaje reciente de un tópico",
    description="Reenvía a `POST /listener` del agente.",
)
async def api_kafka_agent_listener(
    body: Annotated[
        dict[str, Any],
        Body(openapi_examples={"tópico": {"summary": "Escuchar", "value": {"topic": "mi-topico", "max_polls": 3}}}),
    ],
):
    status, content = await run_in_threadpool(_forward, "/listener", body)
    return _response(status, content)


@router.post(
    f"{_PREFIX}/clean-topics",
    operation_id="kafka_agent_clean_topics",
    summary="Borrar tópicos",
    description=(
        "Reenvía a `POST /clean-topics` del agente. **Borra datos.** Con la lista vacía "
        "borra todos los tópicos no internos."
    ),
)
async def api_kafka_agent_clean_topics(
    body: Annotated[
        dict[str, Any],
        Body(
            openapi_examples={
                "dos-tópicos": {
                    "summary": "Borrar dos tópicos",
                    "value": {"body": {"topics": ["mi-topico", "otro-topico"]}},
                }
            }
        ),
    ],
):
    status, content = await run_in_threadpool(_forward, "/clean-topics", body)
    return _response(status, content)
