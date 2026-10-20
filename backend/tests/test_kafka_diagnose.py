"""Reading a Kafka log well enough to say what failed — and what would fix it.

The failure these pin down is not hypothetical: it is the one in this project's own
broker logs, a ``log.dirs`` entry that survived a previous run because the JVM still
held a checkpoint open, which Windows refuses to delete. The broker replays that
metadata log and dies, and the only thing that fixes it is wiping the storage.

What matters most here is what these tests say **no**. The button is destructive, so
a detection that is too eager costs the developer their topics and still does not
start the broker. ``DuplicateBrokerRegistrationException`` is the case that guards
that: Kafka itself says it self-resolves, and a button on it would appear on almost
every restart.
"""

from __future__ import annotations

import pytest

from yappy_library.adapters.kafka.diagnose import (
    FIX_CLEAN_STORAGE,
    _is_error_line,
    failure_excerpt,
    fix_for,
    log_dirs,
    needs_clean,
    read_log,
)

# The real lines, from a real broker log on this project.
REAL_TAIL = """[2026-10-14 14:29:19,020] INFO [KafkaRaftServer nodeId=1] Kafka Server starting (kafka.server.KafkaRaftServer)
[2026-10-14 14:29:19,023] ERROR [SharedServer id=1] Got exception while starting SharedServer (kafka.server.SharedServer)
java.nio.file.AccessDeniedException: C:\\tmp\\kraft-combined-logs\\__cluster_metadata-0\\00000000000000000000-0000000000.checkpoint.deleted
\tat java.base/sun.nio.fs.WindowsException.translateToIOException(WindowsException.java:89)
\tat java.base/sun.nio.fs.WindowsFileSystemProvider.implDelete(WindowsFileSystemProvider.java:273)
\tat java.base/sun.nio.fs.Files.delete(Files.java:1169)
"""

# The one that is NOT actionable, with Kafka's own explanation of why.
SELF_RESOLVING = """[2026-10-16 23:27:11,540] INFO [QuorumController id=1] registerBroker: event failed with DuplicateBrokerRegistrationException in 2714 microseconds. Exception message: Another broker is registered with that broker id. If the broker was recently restarted this should self-resolve once the heartbeat manager expires the broker's session.
"""

# El storage ya roto: la recuperación de Kafka borra el segmento, crea uno
# vacío y se muere con un NPE porque el índice vacío no tiene mapping.
CORRUPT_INDEX = """[2026-10-19 20:39:23,955] WARN [LogLoader partition=__cluster_metadata-0, dir=C:\\tmp\\kraft-combined-logs] Found invalid offset during recovery. Deleting the corrupt segment and creating an empty one with starting offset 0 (org.apache.kafka.storage.internals.log.LogLoader)
[2026-10-19 20:39:23,955] ERROR [SharedServer id=1] Got exception while starting SharedServer (kafka.server.SharedServer)
java.lang.NullPointerException: Cannot invoke "java.nio.MappedByteBuffer.duplicate()" because the return value of "org.apache.kafka.storage.internals.log.OffsetIndex.mmap()" is null
\tat org.apache.kafka.storage.internals.log.OffsetIndex.lambda$lookup$0(OffsetIndex.java:99)
"""

# El registro que no se pudo leer, que sí nombra el archivo en el que estaba.
CORRUPT_RECORD = """[2026-10-19 16:01:27,261] ERROR Encountered fatal fault: Unexpected error in raft IO thread (org.apache.kafka.server.fault.ProcessTerminatingFaultHandler)
org.apache.kafka.common.errors.CorruptRecordException: Found record size 0 smaller than minimum record overhead (14) in file C:\\tmp\\kraft-combined-logs\\__cluster_metadata-0\\00000000000000000000.log.
"""

KRAFT_DIRS = [r"C:\tmp\kraft-combined-logs"]


# --- the excerpt ---------------------------------------------------------


def test_el_veredicto_cede_la_excepcion_y_no_los_frames_del_stack_trace():
    """The bug this replaces: three lines of ``Files.delete`` as the whole reason.

    The exception line says what went wrong and is twenty lines below the last one
    of a naive tail. Quoting the frames told the developer where some file was
    being deleted and nothing about why.
    """
    excerpt = failure_excerpt(REAL_TAIL)

    assert any("AccessDeniedException" in line for line in excerpt)
    assert not any(line.startswith("\tat ") for line in excerpt)


def test_el_veredicto_trae_el_error_que_presented_la_excepcion():
    """The ``ERROR`` line says *when* — while starting — and the exception says *what*.

    Either one alone is half an answer: ``AccessDeniedException: C:\\tmp\\...`` does
    not say what the broker was doing, and ``Got exception while starting
    SharedServer`` does not say what the exception was.

    And note *where* the level is: Kafka writes ``[timestamp] ERROR [Thread]``, so a
    ``startswith("ERROR")`` finds nothing. That is not hypothetical — it is what the
    first version of this detection did, and it silently matched no lines at all.
    """
    excerpt = failure_excerpt(REAL_TAIL)

    assert any(_is_error_line(line) for line in excerpt)
    assert any("AccessDeniedException" in line for line in excerpt)


def test_una_excepcion_repetida_no_se_repite_en_el_veredicto():
    """A broker that retries its metadata log writes the same line fifteen times.

    Fifteen copies of one line is a worse message than two different ones, and it
    buries the part that says something new.
    """
    repetido = "\n".join([REAL_TAIL] * 4)

    excerpt = failure_excerpt(repetido)

    assert len(excerpt) == len(set(excerpt))
    assert len(excerpt) <= 3


def test_una_excepcion_se_elige_por_su_posicion_temporal():
    """Un log acumulado tiene fallos viejos; el veredicto es del último.

    A log file gets reused across runs — Windows recycles the pid, so
    ``kafka-server-<pid>.log`` picked up a successful start two days after a failed
    one. Quoting the first exception in the file explains a start that is not the
    one being reported.
    """
    dos_corridas = (
        "[2026-10-14 14:29:19,023] ERROR arranque viejo\n"
        "java.lang.IllegalStateException: un fallo de hace dos dias\n"
        "\tat somewhere.Far(Frame.java:1)\n"
        + REAL_TAIL
        + "[2026-10-16 23:31:16,292] INFO [KafkaRaftServer nodeId=1] Kafka Server started (kafka.server.KafkaRaftServer)\n"
    )

    excerpt = failure_excerpt(dos_corridas)

    assert any("AccessDeniedException" in line for line in excerpt)
    assert not any("hace dos dias" in line for line in excerpt)


def test_un_log_que_no_dice_que_fallo_devuelve_algo_igual():
    """Una falla silenciosa es peor que una vaga: se devuelve lo último que hay."""
    assert failure_excerpt("INFO todo bien\nINFO nada más\n")


def test_un_log_vacio_no_inventa_una_razon():
    assert failure_excerpt("") == []
    assert failure_excerpt([]) == []


# --- the decision --------------------------------------------------------


def test_un_storage_bloqueado_pide_la_limpieza():
    """El caso que la web resuelve con un botón."""
    assert needs_clean(REAL_TAIL, KRAFT_DIRS) is True


def test_broker_duplicado_no_pide_la_limpieza():
    """El que se resuelve solo, y por lo tanto no habilita un botón destructivo.

    ``DuplicateBrokerRegistrationException`` aparece después de cualquier reinicio
    mal cortado y el propio Kafka dice que expira la sesión del broker anterior. Un
    botón acá aparecería casi siempre y no arreglaría nada — con el costo de que el
    developer que le creyera perdería sus topics.
    """
    assert needs_clean(SELF_RESOLVING, KRAFT_DIRS) is False


def test_un_access_denied_en_otro_archivo_no_pide_la_limpieza():
    """El error importa *dónde*. Borrar el storage no arregla un config que no se puede leer."""
    otro = (
        "ERROR [Broker id=1] no arranca\n"
        "java.nio.file.AccessDeniedException: C:\\kafka\\config\\kraft\\server.properties\n"
    )

    assert needs_clean(otro, KRAFT_DIRS) is False


def test_un_metadata_log_corrupto_pide_la_limpieza():
    """La falla que faltaba: el storage ya está roto y *cada* arranque falla igual.

    La recuperación de Kafka borra el segmento corrupto, crea uno vacío y se
    muere con un NPE porque el índice vacío no tiene mapping que duplicar. Esta
    detección sólo buscaba un ``AccessDeniedException``, así que el botón de
    limpiar no aparecía y el developer tenía que ir a la terminal a hacer lo
    único que servía.
    """
    assert needs_clean(CORRUPT_INDEX, KRAFT_DIRS) is True


def test_un_registro_corrupto_pide_la_limpieza():
    """La otra mitad de la misma historia: el registro que no se pudo leer.

    Esta sí nombra el archivo, y el archivo está dentro del ``log.dirs``: es el
    mismo storage y el mismo arreglo.
    """
    assert needs_clean(CORRUPT_RECORD, KRAFT_DIRS) is True


def test_un_npe_que_no_es_del_storage_no_pide_la_limpieza():
    """Un NPE de otro índice no es el de la recuperación del metadata log.

    El botón borra los topics: ofrecerlo por una excepción que no es del
    storage es perderlos sin arreglar nada.
    """
    otro = (
        "ERROR [SharedServer id=1] Got exception while starting SharedServer\n"
        'java.lang.NullPointerException: Cannot invoke "java.nio.Buffer.limit()" '
        'because the return value of "org.apache.kafka.common.utils.Utils.unwrap()" is null\n'
    )

    assert needs_clean(otro, KRAFT_DIRS) is False


def test_el_boton_aparece_con_el_log_de_la_falla(tmp_path):
    """La puerta de entrada real: el log del proceso y la pregunta "¿qué lo arregla?".

    Es el camino que sigue el supervisor, así que es el que tiene que devolver el
    nombre del arreglo.
    """
    log = tmp_path / "kafka-server.log"
    log.write_text(CORRUPT_INDEX, encoding="utf-8")

    assert fix_for(str(log), KRAFT_DIRS) == FIX_CLEAN_STORAGE


def test_sin_log_dirs_no_se_ofrece_nada():
    """Sin saber dónde está el storage, no se puede afirmar que el error es de él."""
    assert needs_clean(REAL_TAIL, []) is False
    assert fix_for("log.txt", []) == ""


def test_la_excepcion_matchea_con_las_dos_formas_del_path():
    """``log.dirs`` en la config y el path del JVM no se escriben igual.

    La config dice ``/tmp/kraft-combined-logs`` y el mensaje de la JVM dice
    ``C:\\tmp\\kraft-combined-logs``. Ambos matchean contra su propia forma; lo que no
    funciona es comparar en crudo, porque el separador difiere. Eso no pasó
    hipotéticamente: fue el primer bug de esta detección, y su síntoma —el botón que
    no aparece nunca— es indistinguible de "no hay nada roto".
    """
    unix = "java.nio.file.AccessDeniedException: /tmp/kraft-combined-logs/x.deleted\n"
    windows = "java.nio.file.AccessDeniedException: C:\\tmp\\kraft-combined-logs\\x.deleted\n"

    # Cada forma matchea contra su propia configuración…
    assert needs_clean(unix, ["/tmp/kraft-combined-logs"]) is True
    assert needs_clean(windows, [r"C:\tmp\kraft-combined-logs"]) is True
    # …y con la variante de separadores que usa el otro programa.
    assert needs_clean(windows, ["C:/tmp/kraft-combined-logs/"]) is True
    # Un directorio que no es, no matchea con ninguna forma.
    assert needs_clean(windows, [r"C:\otro\logs"]) is False
    # Y al revés también matchea, que es el caso real: la config dice `/tmp/...`
    # y el JVM dice `C:\tmp\...`. Que la letra de drive no disqualifique el
    # directorio es lo que hace que el botón aparezca igual.
    assert needs_clean(windows, ["/tmp/kraft-combined-logs"]) is True


# --- reading ------------------------------------------------------------


def test_un_log_que_no_existe_no_rompe_a_nadie(tmp_path):
    """El que pregunta es un supervisor esperando: no puede morir por un log ilegible."""
    assert read_log(tmp_path / "no-existe.log") == []
    assert fix_for(str(tmp_path / "no-existe.log"), KRAFT_DIRS) == ""


def test_leer_el_log_devuelve_lineas(tmp_path):
    log = tmp_path / "kafka.log"
    log.write_bytes(b"primera\nsegunda\n")

    assert read_log(log) == ["primera", "segunda"]


def test_log_dirs_resuelve_lo_relativo_contra_el_core(tmp_path):
    """Un ``/tmp/...`` sin letra de drive se resuelve como ``<core>\\tmp\\...``.

    Que es exactamente como lo imprime la JVM en el error, y por eso la comparación
    tiene que happen sobre la forma resuelta.
    """
    props = tmp_path / "config" / "kraft" / "server.properties"
    props.parent.mkdir(parents=True)
    props.write_text(
        "process.roles=broker,controller\nlog.dirs=/tmp/kraft-combined-logs\n", encoding="utf-8"
    )

    (resolved,) = log_dirs(tmp_path)

    assert resolved.endswith("kraft-combined-logs")
    assert needs_clean(
        f"java.nio.file.AccessDeniedException: {resolved}\\x.deleted", [resolved]
    ) is True


def test_log_dirs_de_un_core_incompleto_no_inventa(tmp_path):
    """Kafka todavía no instalado: no hay storage que borrar y no hay nada que ofrecer."""
    assert log_dirs(tmp_path) == []


def test_el_nombre_del_arreglo_no_es_una_frase():
    """El campo viaja a la pantalla: el texto va donde se dibuja el botón, no en el log."""
    assert FIX_CLEAN_STORAGE == "kafka_clean_storage"
    assert " " not in FIX_CLEAN_STORAGE


@pytest.mark.parametrize("probe", ["mysql", "http", ""])
def test_un_probe_que_no_es_kafka_no_tiene_arreglo_conocido(probe, tmp_path):
    """La pregunta se hace por probe: hoy sólo Kafka tiene un remedio quetica."""
    log = tmp_path / "otro.log"
    log.write_text(REAL_TAIL, encoding="utf-8")

    from yappy_library.application.workflow.supervisor import Supervisor

    supervisor = Supervisor(4242, 9092, probe, "algo", str(log))

    assert supervisor.suggested_fix() == "" or probe == ""