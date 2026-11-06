"""Kafka Agent: un puente HTTP ↔ Kafka local, portado de ``kafka-agents``.

Corre como proceso propio (``yappy_api.kafka_agent_server``, puerto 9091), igual
que el original corría en su contenedor. La web lo levanta, lo para y lo muestra
como cualquier otro recurso de Kafka local: ver ``application/workflow/registry.py``.

Las conexiones (producer, consumer y admin) se abren la primera vez que hacen
falta y se reutilizan. Si el broker no contesta, la operación falla con
:class:`AgentUnavailable` y el proceso sigue vivo: la próxima llamada vuelve a
intentar la conexión. Por eso no hay estado de "levantado" que mantener acá;
reiniciar el agente es reiniciar el proceso.

Ninguna función de este módulo termina el proceso: los errores son excepciones.
Ver :class:`KafkaUnavailable` en ``service.py`` para el motivo.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yappy_library.config import Config

from .service import _broker_port

#: Polls por búsqueda cuando el request no dice cuántos (``CONSUMER_RETRY_ATTEMPS``
#: en el original).
DEFAULT_MAX_POLLS = 3

#: Tope de una búsqueda entera, por más polls que se pidan. Igual que el original.
SEARCH_TIMEOUT_SECONDS = 10

#: Cuánto espera cada ``poll``.
POLL_TIMEOUT_MS = 1000

#: Cuántos mensajes hacia atrás mira una búsqueda, por poll pedido.
MESSAGES_BACK_PER_POLL = 1000

#: Cuánto espera el agente a que el broker conteste al abrir una conexión.
#:
#: Corto a propósito: con el broker caído, cada request espera esto antes de
#: responder el error. El default de kafka-python son 30 segundos.
BOOTSTRAP_TIMEOUT_MS = 5000

#: Timeout de una publicación y del borrado de tópicos, como en el original.
REQUEST_TIMEOUT_MS = 30000

#: Cuánto puede bloquear ``send`` esperando la metadata del tópico.
PUBLISH_BLOCK_MS = 10000

#: Atributos del CloudEvent que no viajan como header ``ce_*``. Mismo criterio que
#: ``cloudevents.kafka.conversion.to_binary`` (1.12): ``datacontenttype`` va como
#: ``content-type`` y ``partitionkey`` sería la key del mensaje.
_NOT_A_CE_HEADER = ("data", "partitionkey", "datacontenttype")


class AgentUnavailable(Exception):
    """El agente no puede hacer lo que se le pidió, y el motivo para mostrar."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def bootstrap_servers(cfg: Config) -> list[str]:
    """Contra qué broker habla el agente.

    ``KAFKA_BOOTSTRAP_SERVERS`` gana si está (``host:puerto`` separados por coma).
    Si no, ``127.0.0.1`` y el puerto de clientes del broker local, leído de su
    propia configuración.

    ``127.0.0.1`` y no ``localhost``: en Windows ``localhost`` resuelve primero a
    ``::1``, y kafka-python 3 agota el bootstrap ahí sin probar IPv4.
    """
    raw = cfg.get("KAFKA_BOOTSTRAP_SERVERS")
    if raw:
        servers = [s.strip() for s in raw.split(",") if s.strip()]
        if servers:
            return servers
    props = Path(cfg.kafka_core_path) / "config" / "kraft" / "server.properties"
    return [f"127.0.0.1:{_broker_port(props)}"]


# --- CloudEvents en modo binario ------------------------------------------------


def cloud_event_record(
    cloud_event_id: str, event_type: str, headers: dict[str, Any], message: Any,
) -> tuple[list[tuple[str, bytes]], bytes]:
    """Los headers y el value de un CloudEvent en modo binario de Kafka.

    Reproduce lo que hacía el original con ``cloudevents``
    (``CloudEvent.create`` + ``conversion.to_binary(data_marshaller=str)``) sin
    depender de la librería: la 1.9 —la que fija ``yappy-common-library`` en la
    misma máquina— exige un atributo ``content-type`` que el original nunca
    mandaba, y la 2.x ya no tiene ``cloudevents.kafka``.

    - Los atributos son los headers del request más ``id``, ``type``,
      ``datacontenttype=application/json`` y ``source=source`` —pisando lo que
      viniera, igual que el original—, con las claves en minúscula.
    - ``specversion`` y ``time`` se completan si faltan.
    - Cada atributo va como ``ce_<nombre>``, salvo ``datacontenttype``, que va
      como ``content-type``, y ``partitionkey``, que no viaja.
    - El value es ``json.dumps(message)`` en UTF-8.
    """
    attributes: dict[str, Any] = {str(k).lower(): v for k, v in headers.items()}
    attributes.update(
        {
            "id": cloud_event_id,
            "datacontenttype": "application/json",
            "type": event_type,
            "source": "source",
        }
    )
    attributes.setdefault("specversion", "1.0")
    attributes.setdefault("time", datetime.now(UTC).isoformat())

    record_headers: list[tuple[str, bytes]] = [
        ("content-type", str(attributes["datacontenttype"]).encode("utf-8"))
    ]
    for name, value in attributes.items():
        if name in _NOT_A_CE_HEADER or value is None:
            continue
        record_headers.append((f"ce_{name}", str(value).encode("utf-8")))
    return record_headers, json.dumps(message).encode("utf-8")


# --- lectura de mensajes ----------------------------------------------------------


class _Undecodable(Exception):
    """Un mensaje que coincidió pero cuyo value no es JSON en UTF-8."""


def _decode_headers(raw: list | None) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    for key, value in raw or []:
        if value is None:
            headers[key] = None
        elif isinstance(value, bytes):
            headers[key] = value.decode("utf-8", errors="replace")
        else:
            headers[key] = value
    return headers


def _envelope(message, headers: dict[str, Any]) -> dict[str, Any]:
    """``{"data": {"key", "value", "headers"}}``, la forma de respuesta del original."""
    try:
        value = json.loads(message.value.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Undecodable(str(exc)) from exc
    key = message.key.decode("utf-8", errors="replace") if message.key else ""
    return {"data": {"key": key, "value": value, "headers": headers}}


# --- clientes de kafka-python -------------------------------------------------------


def _default_producer(servers: list[str]):
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=servers,
        acks=1,
        retries=5,
        request_timeout_ms=REQUEST_TIMEOUT_MS,
        bootstrap_timeout_ms=BOOTSTRAP_TIMEOUT_MS,
        max_block_ms=PUBLISH_BLOCK_MS,
    )


def _default_consumer(servers: list[str]):
    from kafka import KafkaConsumer

    # Sin group_id: asignación manual de particiones, como el original.
    return KafkaConsumer(
        bootstrap_servers=servers,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        request_timeout_ms=15000,
        fetch_max_wait_ms=500,
        fetch_min_bytes=1,
        max_partition_fetch_bytes=1048576,
        max_poll_records=1000,
        bootstrap_timeout_ms=BOOTSTRAP_TIMEOUT_MS,
    )


def _default_admin(servers: list[str]):
    from kafka.admin import KafkaAdminClient

    return KafkaAdminClient(
        bootstrap_servers=servers,
        request_timeout_ms=REQUEST_TIMEOUT_MS,
        bootstrap_timeout_ms=BOOTSTRAP_TIMEOUT_MS,
    )


def _close_quietly(client, **kwargs) -> None:
    if client is None:
        return
    try:
        client.close(**kwargs)
    except Exception:  # noqa: BLE001 - cerrar no puede romper una operación
        pass


class KafkaAgent:
    """El producer y el consumer del agente, abiertos bajo demanda."""

    def __init__(
        self,
        producer_factory: Callable[[list[str]], Any] = _default_producer,
        consumer_factory: Callable[[list[str]], Any] = _default_consumer,
        admin_factory: Callable[[list[str]], Any] = _default_admin,
        config_factory: Callable[[], Config] = Config,
    ):
        self._producer_factory = producer_factory
        self._consumer_factory = consumer_factory
        self._admin_factory = admin_factory
        self._config_factory = config_factory
        self._cfg: Config | None = None
        # El producer se comparte entre hilos; su creación va con lock.
        self._producer_lock = threading.Lock()
        # El consumer no es thread-safe: toda lectura va serializada con este lock.
        self._consumer_lock = threading.Lock()
        self._producer: Any = None
        self._consumer: Any = None

    def _servers(self) -> list[str]:
        if self._cfg is None:
            self._cfg = self._config_factory()
        return bootstrap_servers(self._cfg)

    def _open(self, factory: Callable[[list[str]], Any]) -> Any:
        servers = self._servers()
        try:
            return factory(servers)
        except ImportError as exc:
            raise AgentUnavailable(
                f"Falta la librería de Kafka ({exc.name}). Instalala con `pip install -e .`."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de bootstrap es el mismo motivo
            raise AgentUnavailable(
                f"No se pudo conectar al broker en {', '.join(servers)}: {exc}. "
                "¿Está levantado el Kafka Server?"
            ) from exc

    def _producer_handle(self) -> Any:
        with self._producer_lock:
            if self._producer is None:
                self._producer = self._open(self._producer_factory)
            return self._producer

    def _consumer_handle(self) -> Any:
        """Llamar sólo con ``_consumer_lock`` tomado."""
        if self._consumer is None:
            self._consumer = self._open(self._consumer_factory)
        return self._consumer

    def close(self) -> None:
        """Cerrar las conexiones. Lo llama el proceso al apagarse."""
        with self._producer_lock:
            producer, self._producer = self._producer, None
        with self._consumer_lock:
            consumer, self._consumer = self._consumer, None
        _close_quietly(producer)
        _close_quietly(consumer, autocommit=False)

    # --- producer -----------------------------------------------------------------

    def publish(
        self, topic: str, cloud_event_id: str, message: Any,
        headers: dict[str, Any], event_type: str,
    ) -> None:
        """Publicar ``message`` como CloudEvent y esperar el ack del broker."""
        producer = self._producer_handle()
        record_headers, value = cloud_event_record(cloud_event_id, event_type, headers, message)
        future = producer.send(topic, value=value, headers=record_headers)
        future.get(timeout=REQUEST_TIMEOUT_MS / 1000)

    # --- consumer -----------------------------------------------------------------

    def find_by_header(
        self, header_key: str, header_value: str, topic: str,
        max_polls: int = DEFAULT_MAX_POLLS,
    ) -> list[dict[str, Any]] | None:
        """Los mensajes con ``header_key == header_value``.

        Como el original: corta en el primer poll que trajo coincidencias. ``None``
        es "no está".
        """
        return self._scan(topic, max_polls, lambda h: h.get(header_key) == header_value, False)

    def listen(self, topic: str, max_polls: int = DEFAULT_MAX_POLLS) -> dict[str, Any] | None:
        """El primer mensaje que aparezca entre los recientes (``/listener``)."""
        found = self._scan(topic, max_polls, lambda _h: True, True)
        return found[0] if found else None

    def _scan(
        self, topic: str, max_polls: int, matches: Callable[[dict[str, Any]], bool],
        first_only: bool,
    ) -> list[dict[str, Any]] | None:
        """Buscar entre los últimos ``1000 * max_polls`` mensajes de cada partición.

        ``None`` es "no está" —tópico inexistente, nada que coincida o un value que
        no es JSON—. Un error del broker sube como excepción.
        """
        from kafka.structs import TopicPartition

        max_polls = max(1, max_polls)
        with self._consumer_lock:
            consumer = self._consumer_handle()
            try:
                partitions = consumer.partitions_for_topic(topic)
                if not partitions:
                    return None
                assigned = [TopicPartition(topic, p) for p in partitions]
                consumer.assign(assigned)
                end_offsets = consumer.end_offsets(assigned)
                for tp in assigned:
                    back = MESSAGES_BACK_PER_POLL * max_polls
                    consumer.seek(tp, max(0, end_offsets[tp] - back))

                deadline = time.time() + SEARCH_TIMEOUT_SECONDS
                found: list[dict[str, Any]] = []
                for _ in range(max_polls):
                    if time.time() > deadline:
                        break
                    records = consumer.poll(timeout_ms=POLL_TIMEOUT_MS)
                    for batch in (records or {}).values():
                        for message in batch:
                            headers = _decode_headers(message.headers)
                            if not matches(headers):
                                continue
                            found.append(_envelope(message, headers))
                            if first_only:
                                return found
                    if found:
                        return found
                return None
            except _Undecodable:
                return None
            except Exception:
                # Un consumer que falló puede quedar en un estado que no sirve:
                # se descarta y la próxima búsqueda abre uno nuevo.
                _close_quietly(self._consumer, autocommit=False)
                self._consumer = None
                raise

    # --- admin --------------------------------------------------------------------

    def delete_topics(self, topics: list[str] | None) -> dict[str, Any]:
        """Borrar ``topics``, o todos los no internos si la lista viene vacía.

        Devuelve la respuesta del original con su ``status.code``: 200, 207 (alguno
        no existía o falló), 404 (ninguno existía).
        """
        with self._consumer_lock:
            consumer = self._consumer_handle()
            existing = sorted(t for t in set(consumer.topics()) if not t.startswith("__"))

        not_found: list[str] = []
        if topics:
            not_found = [t for t in topics if t not in existing]
            targets = [t for t in topics if t in existing]
            if not targets:
                return {
                    "status": {"code": 404, "message": "Ninguno de los tópicos especificados existe"},
                    "data": {"topics_deleted": [], "topics_not_found": not_found},
                }
        else:
            targets = existing
        if not targets:
            return {
                "status": {"code": 200, "message": "No hay tópicos para limpiar"},
                "data": {"topics_deleted": []},
            }

        admin = self._admin_factory(self._servers())
        try:
            result = admin.delete_topics(targets, timeout_ms=REQUEST_TIMEOUT_MS, raise_errors=False)
        finally:
            _close_quietly(admin)

        codes = {
            entry.get("name"): entry.get("error_code", -1)
            for entry in (result or {}).get("topics", [])
            if isinstance(entry, dict)
        }
        deleted = [t for t in targets if codes.get(t, -1) == 0]
        failed = [
            {"topic": t, "error": f"Error code: {codes.get(t, -1)}"}
            for t in targets if codes.get(t, -1) != 0
        ]

        data: dict[str, Any] = {"topics_deleted": deleted}
        if not_found:
            data["topics_not_found"] = not_found
        if failed:
            data["topics_failed"] = failed
            return {
                "status": {"code": 207, "message": "Algunos tópicos no pudieron ser eliminados"},
                "data": data,
            }
        if not_found:
            return {
                "status": {
                    "code": 207,
                    "message": "Algunos tópicos no existen, los demás fueron eliminados",
                },
                "data": data,
            }
        return {"status": {"code": 200, "message": "Tópicos eliminados exitosamente"}, "data": data}


#: El agente del proceso. Uno solo: las rutas lo comparten.
agent = KafkaAgent()
