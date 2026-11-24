"""Kafka Agent en la API principal: el proceso del agente, visto desde ``/docs``.

Las rutas reales viven en el proceso del agente (``yappy_api.kafka_agent_server``,
puerto 9091) con el contrato de ``kafka-agents``. Estas las repiten bajo
``/api/kafka-agent`` para que aparezcan en ``/docs`` y en ``openapi.json`` junto
al resto de la API. Reenvían el body tal cual y devuelven la respuesta del agente
con su mismo status code.

Si el agente no está levantado, las operaciones contestan 409. Si está levantado
pero no contesta a tiempo, 503. El estado es la excepción: contesta 200 con
``ok: false`` para que la pantalla pueda dibujar "apagado" sin tratarlo como un
error de red.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Annotated, Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from yappy_library.adapters.kafka.agent_smoke import run_smoke
from yappy_library.adapters.kafka.service import KAFKA_AGENT_PORT

from ..schemas import KafkaAgentSmokeRequest, KafkaAgentSmokeResponse, KafkaAgentStatusResponse

router = APIRouter(tags=["kafka-agent"])

_PREFIX = "/api/kafka-agent"

#: Una búsqueda puede tardar lo que tarda el agente en recorrer el tópico (hasta ~10s).
_TIMEOUT_SECONDS = 60

_NOT_RUNNING_MESSAGE = "El Kafka Agent no está levantado. Levantalo desde la sección Kafka local."

_NOT_RUNNING = {
    "status": {"code": 409, "message": _NOT_RUNNING_MESSAGE},
    "detail": _NOT_RUNNING_MESSAGE,
    "data": None,
}


def _request(
    path: str, body: Any = None, method: str = "POST",
) -> tuple[int, Any]:
    """Llamar al agente y devolver ``(status, json)``, traduciendo los fallos de red."""
    url = f"http://127.0.0.1:{KAFKA_AGENT_PORT}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return 409, _NOT_RUNNING
        detail = f"El Kafka Agent no respondió: {exc.reason}"
        return 503, {
            "status": {"code": 503, "message": detail},
            "detail": detail,
            "data": None,
        }
    except TimeoutError:
        detail = "El Kafka Agent no respondió a tiempo"
        return 503, {
            "status": {"code": 503, "message": detail},
            "detail": detail,
            "data": None,
        }


def _forward(path: str, body: Any) -> tuple[int, Any]:
    return _request(path, body)


def _response(status: int, content: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content=content)


@router.get(
    f"{_PREFIX}/status",
    operation_id="kafka_agent_status",
    summary="Estado del Kafka Agent",
    description="Pregunta `GET /health` al proceso del agente. Apagado es `ok: false`, no un error.",
    response_model=KafkaAgentStatusResponse,
)
async def api_kafka_agent_status():
    status, content = await run_in_threadpool(_request, "/health", None, "GET")
    if status != 200 or not isinstance(content, dict) or not content.get("ok"):
        return _response(
            200,
            {"ok": False, "port": KAFKA_AGENT_PORT, "brokers": []},
        )
    return _response(200, content)


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
    summary="Buscar un mensaje por cloud_event_id",
    description=(
        "Reenvía a `POST /consumer` del agente: busca el header `ce_id` en el tópico. "
        "Para buscar por otro header, usá `/v2/consumer`."
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
                        "cloud_event_id": "evt-001",
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
    f"{_PREFIX}/v2/consumer",
    operation_id="kafka_agent_consumer_v2",
    summary="Buscar mensajes por un header key/value",
    description="Reenvía a `POST /v2/consumer` del agente, la búsqueda por header del repo original.",
)
async def api_kafka_agent_consumer_v2(
    body: Annotated[
        dict[str, Any],
        Body(
            openapi_examples={
                "por-header": {
                    "summary": "Buscar por header propio",
                    "value": {
                        "header_key": "customheader",
                        "header_value": "valor1",
                        "topic": "mi-topico",
                        "max_polls": 3,
                    },
                }
            }
        ),
    ],
):
    status, content = await run_in_threadpool(_forward, "/v2/consumer", body)
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


@router.post(
    f"{_PREFIX}/tests/run",
    operation_id="kafka_agent_run_tests",
    summary="Ejecutar la prueba local del agente",
    description=(
        "Publica un evento de prueba y comprueba que aparece por `/consumer`, "
        "`/v2/consumer` y `/listener`. Es el equivalente Python de la prueba "
        "end-to-end del repo original, sin Docker ni Java."
    ),
    response_model=KafkaAgentSmokeResponse,
)
async def api_kafka_agent_run_tests(body: KafkaAgentSmokeRequest):
    status, _ = await run_in_threadpool(_request, "/health", None, "GET")
    if status == 409:
        return _response(409, _NOT_RUNNING)
    report = await run_in_threadpool(
        run_smoke,
        _request,
        body.topic,
        body.messages,
        body.max_polls,
        body.cleanup,
    )
    return _response(200, report)
