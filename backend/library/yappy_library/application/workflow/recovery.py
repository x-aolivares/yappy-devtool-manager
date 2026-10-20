"""Rescatar un arranque de Kafka que falló porque el storage quedó roto.

El broker died con "Found invalid offset during recovery" y después con un NPE
de un índice vacío. La causa es que el metadata log quedó inconsistente —por un
broker duplicado o por un apagado a la fuerza— y Kafka no lo puede reparar:
borra el segmento, crea uno vacío, y se muere.

De ahí en adelante **todos** los arranques fallan igual, y lo único que los
resuelve es borrar el storage y formatearlo de nuevo. Eso es exactamente lo que
hace el botón de "Limpiar el broker" de la pantalla, y hasta ahora era una
tarea de terminal: el developer tenía que adivinar que el storage era el
problema y hacer los dos clicks en el orden correcto.

Este módulo hace esa parte sola: limpia, levanta el broker de nuevo y deja que
un supervisor lo vigile —**sin** permiso de rescatarse otra vez, para que un
storage que no se arregla no se convierta en un loop de borrados**.

**Lo que cuesta, y por qué está valido.** El storage tiene los topics locales,
y borrarlo los borra. Pero un broker con el metadata log corrupto no arranca:
los topics que guardaba ya no son alcanzables, y el camino de vuelta es el
mismo borrado. La diferencia es que acá nadie tiene que saber eso.
"""

from __future__ import annotations

import os

from yappy_library.adapters import process_tracker
from yappy_library.adapters.logging import error as log_error
from yappy_library.adapters.logging import info as log_info
from yappy_library.application.workflow.models import STATE_STARTING
from yappy_library.application.workflow.supervisor import supervise
from yappy_library.config import Config


def recover_broken_kafka_storage(owner: str) -> bool:
    """Clean the broken storage and start the broker again.

    ``owner`` is who asked for the broker in the first place —the web or the
    terminal— and the new one is attributed the same way, so the row that shows
    up is the row the developer was already looking at.

    Returns whether the broker was started again. ``False`` means the storage
    could not be cleaned, and the failure stays on the screen with the button
    that cleans it.
    """
    from yappy_library.adapters.kafka.service import KafkaService, KafkaUnavailable

    service = KafkaService(Config())

    log_info(
        "  [wf] el storage de Kafka no arranca: se limpia y se vuelve a levantar. "
        "Los topics locales se pierden --ya no eran alcanzables--."
    )

    try:
        service.clean()
    except KafkaUnavailable as exc:
        log_error(f"  [wf] el storage de Kafka no se pudo limpiar: {exc.reason}")
        return False

    try:
        proc = service.up("server", detach=True, owner=owner)
    except KafkaUnavailable as exc:
        log_error(f"  [wf] Kafka no se pudo volver a levantar: {exc.reason}")
        return False
    if proc is None:
        return False

    # The service already tracked it; the tracker knows the port it listens on
    # and where its log is, and both are what the new supervisor needs.
    entry = next(
        (e for e in process_tracker.get_tracked_processes() if e.get("pid") == proc.pid),
        {},
    )
    port = int(entry.get("local_port") or 0)
    log_file = entry.get("log_file") or ""

    process_tracker.track_process_state_and_supervisor(proc.pid, STATE_STARTING, os.getpid())
    supervise(
        proc.pid,
        port,
        "kafka",
        "Kafka Server",
        log_file=log_file,
        # The one thing this supervisor may not do: rescue itself again. A
        # storage that does not get fixed has to stay visible with its button,
        # not turn into a loop that wipes and starts every few seconds.
        allow_recovery=False,
    )
    log_info("  [wf] Kafka se volvió a levantar con el storage limpio")
    return True
