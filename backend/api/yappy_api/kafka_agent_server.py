"""El Kafka Agent como proceso propio: los endpoints de ``kafka-agents``, tal cual.

Es el mismo contrato que el Flask original, en el mismo puerto (9091) y con las
mismas rutas, para que los clientes existentes (Postman, JMeter, Karate, scripts)
no cambien nada:

=============  ==============  ==========================================
Método         Ruta            Qué hace
=============  ==============  ==========================================
POST           ``/producer``   Publica ``message``/``payload`` como CloudEvent
POST           ``/consumer``   Busca mensajes por header ``header_key=header_value``
POST           ``/listener``   Primer mensaje reciente de un tópico
POST           ``/clean-topics`` Borra tópicos (o todos si la lista viene vacía)
=============  ==============  ==========================================

Los códigos de respuesta y los cuerpos son los del original, incluido lo que el
original hacía ante un error del broker: ``/producer`` contesta 500 y
``/consumer`` contesta 404. No se agregó ninguna precondición de "agente
levantado": el proceso existe si está levantado, y si el broker no contesta, las
rutas responden como respondía el original.

Lo levanta y lo para la web como a cualquier recurso de Kafka local (ver
``application/workflow/registry.py``), con ``python -m yappy_api.kafka_agent_server``.

Escucha sólo en ``127.0.0.1``. El original escuchaba en todas las interfaces
dentro de Docker; fuera de un contenedor, abrirlo a la red no tiene sentido.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from yappy_library.adapters.kafka import agent as kafka_agent
from yappy_library.adapters.kafka.service import KAFKA_AGENT_PORT

logger = logging.getLogger("kafka_agent")

app = FastAPI(title="Kafka Agent", version="1.0.0")


async def _json_body(request: Request) -> dict[str, Any]:
    """El body como dict; cualquier otra cosa (sin body, JSON roto, lista) es ``{}``."""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - el original tampoco distinguía el motivo
        return {}
    return data if isinstance(data, dict) else {}


def _is_json(request: Request) -> bool:
    return "application/json" in request.headers.get("content-type", "")


def _default_max_polls() -> int:
    """``CONSUMER_RETRY_ATTEMPS``, igual que el original (default 3)."""
    return int(os.getenv("CONSUMER_RETRY_ATTEMPS", "3"))


# --- /producer ----------------------------------------------------------------------


def _publish(body: dict[str, Any], message: Any) -> int:
    """200 si el broker confirmó, 500 si no. Igual que ``send_message`` del original."""
    try:
        kafka_agent.agent.publish(
            topic=body.get("topic") or "",
            cloud_event_id=body.get("id") or body.get("cloud_event_id") or "",
            message=message,
            headers=dict(body.get("headers") or {}),
            event_type=body.get("type") or "",
        )
    except Exception:  # noqa: BLE001 - el original tragaba la excepción y devolvía 500
        logger.exception("Error al enviar el mensaje")
        return 500
    return 200


@app.api_route("/producer", methods=["GET", "POST"])
async def producer(request: Request):
    if request.method != "POST":
        return JSONResponse(
            status_code=400,
            content={"status": {"code": 400, "message": "Error al publicar"}, "data": None},
        )
    body = await _json_body(request)
    message = body.get("message") or body.get("payload")
    code = await run_in_threadpool(_publish, body, message)
    return JSONResponse(
        status_code=code,
        content={
            "status": {
                "code": code,
                "message": "Publicacion exitosa" if code == 200 else "Error al publicar",
            },
            "data": body,
        },
    )


# --- /consumer (búsqueda por header) -----------------------------------------------


def _find(header_key: Any, header_value: Any, topic: Any, max_polls: int):
    """Los mensajes encontrados, o ``None``. Un error del broker es ``None``: el original también."""
    try:
        return kafka_agent.agent.find_by_header(header_key, header_value, topic, max_polls)
    except Exception:  # noqa: BLE001
        logger.exception("Error al buscar mensajes")
        return None


@app.api_route("/consumer", methods=["GET", "POST"])
async def consumer(request: Request):
    if request.method != "POST":
        return JSONResponse(status_code=400, content={"data": None})
    start = time.time()
    body = await _json_body(request)
    max_polls = int(body.get("max_polls", _default_max_polls()))
    result = await run_in_threadpool(
        _find, body.get("header_key"), body.get("header_value"), body.get("topic"), max_polls,
    )
    elapsed_ms = round((time.time() - start) * 1000, 2)
    if result is None:
        return JSONResponse(status_code=404, content={"data": None, "response_time_ms": elapsed_ms})
    return JSONResponse(status_code=200, content={"data": result, "response_time_ms": elapsed_ms})


# --- /listener ----------------------------------------------------------------------


def _listen(topic: str, max_polls: int):
    try:
        return kafka_agent.agent.listen(topic, max_polls)
    except Exception:  # noqa: BLE001
        logger.exception("Error al escuchar el tópico")
        return None


@app.post("/listener")
async def listener(request: Request):
    body = await _json_body(request)
    topic = body.get("topic")
    max_polls = int(body.get("max_polls", 3))
    if not topic:
        return JSONResponse(
            status_code=400,
            content={
                "status": {"code": 400, "message": "El campo 'topic' es requerido"},
                "data": None,
            },
        )
    result = await run_in_threadpool(_listen, topic, max_polls)
    if result is None:
        return JSONResponse(status_code=404, content={"data": None})
    return JSONResponse(status_code=200, content=result)


# --- /clean-topics ------------------------------------------------------------------


def _clean(topics: list[str]) -> dict[str, Any]:
    try:
        return kafka_agent.agent.delete_topics(topics or None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error al eliminar tópicos")
        return {
            "status": {"code": 500, "message": f"Error al eliminar tópicos: {exc}"},
            "data": {"topics_deleted": []},
        }


@app.post("/clean-topics")
async def clean_topics(request: Request):
    request_data = await _json_body(request) if _is_json(request) else {}
    body = request_data.get("body", {})
    if not isinstance(body, dict):
        body = {}
    topics = body.get("topics", [])

    if not isinstance(topics, list):
        return JSONResponse(
            status_code=400,
            content={
                "status": {
                    "code": 400,
                    "message": "El campo 'topics' debe ser una lista de strings",
                },
                "data": None,
            },
        )
    if topics and not all(isinstance(topic, str) for topic in topics):
        return JSONResponse(
            status_code=400,
            content={
                "status": {"code": 400, "message": "Todos los elementos de 'topics' deben ser strings"},
                "data": None,
            },
        )

    result = await run_in_threadpool(_clean, topics)
    return JSONResponse(status_code=result["status"]["code"], content=result)


def main() -> None:
    """Levantar el agente en 127.0.0.1:9091. Bloquea hasta Ctrl+C o hasta que lo paren."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    try:
        uvicorn.run(app, host="127.0.0.1", port=KAFKA_AGENT_PORT)
    finally:
        kafka_agent.agent.close()


if __name__ == "__main__":
    main()
