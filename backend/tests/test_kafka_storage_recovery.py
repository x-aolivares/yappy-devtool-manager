"""El storage roto se rescata solo, y una sola vez.

El broker muere con "Found invalid offset during recovery" y con un NPE de un
índice vacío, y del storage que quedó roto sale exactamente lo mismo: **todos**
los arranques fallan hasta que alguien borra el storage a mano. Eso era una
tarea de terminal, y el developer tenía que adivinar que el storage era el
problema.

Estos tests fijan las dos mitades del arreglo:

- **el supervisor lo resuelve solo** cuando la falla es la que el storage
  arregla, con el mismo dueño que pidió el broker la primera vez;
- **una sola vez**: el supervisor del broker nuevo no puede rescatarse otra
  vez, así que un storage que no se arregla se queda visible con su botón en
  vez de convertirse en un loop de borrados.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import yappy_library.adapters.kafka.service as kafka_service
from yappy_library.adapters import process_tracker
from yappy_library.adapters.kafka.service import KafkaUnavailable
from yappy_library.application.workflow import recovery as recovery_mod
from yappy_library.application.workflow import supervisor as supervisor_mod
from yappy_library.application.workflow.supervisor import Supervisor

#: El NPE con el que muere el broker cuando el metadata log quedó corrupto.
CORRUPT_INDEX = """[2026-10-19 20:39:23,955] ERROR [SharedServer id=1] Got exception while starting SharedServer (kafka.server.SharedServer)
java.lang.NullPointerException: Cannot invoke "java.nio.MappedByteBuffer.duplicate()" because the return value of "org.apache.kafka.storage.internals.log.OffsetIndex.mmap()" is null
"""

#: Un puerto donde nada escucha, para que el probe falle sin esperar.
DEAD_PORT = 1


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


@pytest.fixture
def never_up(monkeypatch):
    """A probe that fails instantly, so the test does not wait for the port."""
    monkeypatch.setattr(
        supervisor_mod, "probe_for", lambda kind: (lambda port, timeout=5.0: False)
    )


@pytest.fixture
def broken_storage(tmp_path, monkeypatch):
    """A log that says the storage is the problem, and the dirs to compare it with."""
    log = tmp_path / "kafka-server.log"
    log.write_text(CORRUPT_INDEX, encoding="utf-8")
    monkeypatch.setattr(
        supervisor_mod, "log_dirs", lambda core: [r"C:\tmp\kraft-combined-logs"]
    )
    return log


def _track(log_file: str, **extra):
    process_tracker.track_process(
        pid=os.getpid(),
        resource="kafka",
        target="server",
        owner=process_tracker.OWNER_WEB,
        state="starting",
        local_port=DEAD_PORT,
        log_file=log_file,
        **extra,
    )


# --- el supervisor lo resuelve solo --------------------------------------------


def test_un_storage_roto_se_rescata_solo(tracker, never_up, broken_storage, monkeypatch):
    """La falla que el arreglo conocido arregla: el storage.

    Sin esto, el developer tenía que leer el log del broker, entender que el
    problema era el storage e ir a la terminal a hacer lo único que servía.
    """
    rescued = []
    monkeypatch.setattr(
        recovery_mod,
        "recover_broken_kafka_storage",
        lambda owner: rescued.append(owner) or True,
    )
    _track(str(broken_storage))

    Supervisor(
        pid=os.getpid(), port=DEAD_PORT, probe="kafka", log_file=str(broken_storage),
    ).run(timeout=0.01)

    # Con el mismo dueño: la fila que aparece es la que el developer ya miraba.
    assert rescued == [process_tracker.OWNER_WEB]


def test_el_rescate_no_se_repite(tracker, never_up, broken_storage, monkeypatch):
    """El broker nuevo no puede rescatarse a sí mismo.

    Sin esto, un storage que no se arregla —un archivo que otro proceso tiene
    tomado— se convierte en un loop que borra y levanta cada pocos segundos.
    """
    rescued = []
    monkeypatch.setattr(
        recovery_mod,
        "recover_broken_kafka_storage",
        lambda owner: rescued.append(owner) or True,
    )
    _track(str(broken_storage))

    Supervisor(
        pid=os.getpid(), port=DEAD_PORT, probe="kafka", log_file=str(broken_storage),
        allow_recovery=False,
    ).run(timeout=0.01)

    assert rescued == []


def test_un_tunel_no_se_rescata(tracker, never_up, broken_storage, monkeypatch):
    """Sólo Kafka tiene un storage con esa forma.

    Un túnel que muere no tiene nada que limpiar: su arreglo es otro, y este
    módulo no lo conoce.
    """
    rescued = []
    monkeypatch.setattr(
        recovery_mod,
        "recover_broken_kafka_storage",
        lambda owner: rescued.append(owner) or True,
    )
    _track(str(broken_storage))

    Supervisor(
        pid=os.getpid(), port=DEAD_PORT, probe="http", log_file=str(broken_storage),
    ).run(timeout=0.01)

    assert rescued == []


def test_una_falla_que_no_es_del_storage_no_se_rescata(tracker, never_up, tmp_path,
                                                        monkeypatch):
    """La firma tiene que ser la del storage: si no, no se toca nada."""
    log = tmp_path / "kafka-server.log"
    log.write_text(
        "[2026-10-19 20:39:23,955] ERROR [Broker id=1] no arranca\n"
        "java.lang.OutOfMemoryError: Java heap space\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        supervisor_mod, "log_dirs", lambda core: [r"C:\tmp\kraft-combined-logs"]
    )
    rescued = []
    monkeypatch.setattr(
        recovery_mod,
        "recover_broken_kafka_storage",
        lambda owner: rescued.append(owner) or True,
    )
    _track(str(log))

    Supervisor(
        pid=os.getpid(), port=DEAD_PORT, probe="kafka", log_file=str(log),
    ).run(timeout=0.01)

    assert rescued == []


# --- el rescate en sí -----------------------------------------------------------


def test_el_rescate_limpia_el_storage_y_vuelve_a_levantar(tracker, monkeypatch):
    """El orden es el que importa: limpiar, levantar, y vigilar el nuevo."""
    events = []

    class FakeService:
        def __init__(self, cfg=None):
            events.append("service")

        def clean(self):
            events.append("clean")

        def up(self, target, detach=False, owner="cli"):
            events.append(("up", target, detach, owner))
            process_tracker.track_process(
                pid=4242, resource="kafka", target="server", owner=owner,
                local_port=9092, log_file="kafka-server-4242.log",
            )
            return SimpleNamespace(pid=4242)

    monkeypatch.setattr(kafka_service, "KafkaService", FakeService)
    supervised = []
    monkeypatch.setattr(
        recovery_mod, "supervise", lambda *a, **kw: supervised.append((a, kw))
    )

    ok = recovery_mod.recover_broken_kafka_storage(process_tracker.OWNER_WEB)

    assert ok is True
    # El orden es el que importa: primero se limpia, después se levanta.
    assert events[1] == "clean"
    assert events[2] == ("up", "server", True, process_tracker.OWNER_WEB)
    # El supervisor del broker nuevo no puede rescatarse otra vez.
    assert supervised[0][1]["allow_recovery"] is False


def test_el_rescate_no_insiste_si_el_storage_no_se_puede_limpiar(monkeypatch):
    """Si la limpieza falla, no se levanta nada: el loop se corta acá."""
    class FakeService:
        def __init__(self, cfg=None):
            pass

        def clean(self):
            raise KafkaUnavailable("Error formateando storage")

        def up(self, *a, **kw):
            raise AssertionError("no debe levantar un broker con el storage roto")

    monkeypatch.setattr(kafka_service, "KafkaService", FakeService)

    assert recovery_mod.recover_broken_kafka_storage(process_tracker.OWNER_WEB) is False
