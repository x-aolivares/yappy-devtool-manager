"""Lo que el supervisor dice cuando un arranque falla.

Tres fallos reales de esta tanda se presentaron igual: Kafdrop murio leyendo un
YAML con un byte cp1252, un tunel de SSM murio con el token vencido, y los dos
terminaron con el mismo veredicto -- *"abrio el puerto pero no respondio"* --
que manda a mirar la red cuando la red nunca fue el problema.

Estos tests fijan que un proceso muerto se reporte como muerto, y que el motivo
que el proceso escribio en su propio log llegue al verdict intacto.
"""

from __future__ import annotations

import os

import pytest

from yappy_library.adapters import process_tracker
from yappy_library.application.workflow import supervisor as supervisor_mod
from yappy_library.application.workflow.supervisor import Supervisor

#: A port nothing listens on, so the probe fails immediately instead of hanging
#: on whatever the machine running the suite happens to have open.
DEAD_PORT = 1


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    directory = tmp_path / ".yappy" / "tracker"
    monkeypatch.setattr(process_tracker, "_TRACKER_DIR", directory)
    monkeypatch.setattr(process_tracker, "_parsed_cache", {})
    return directory


def _track(**extra):
    """Register this process in the isolated tracker the conftest points us at."""
    process_tracker.track_process(
        pid=os.getpid(), resource="tunnel", target="t", owner="web",
        state="starting", **extra,
    )


def _forget():
    process_tracker.untrack_process(os.getpid())


@pytest.fixture
def never_up(monkeypatch):
    """A probe that fails instantly.

    Without this the tests about the wait itself are really tests about how fast
    a refused connect is on this OS: connecting to a dead port costs up to the
    probe's own two-second timeout, twice over, which is the same order as the
    behaviour under test. A dead port is a real thing to measure once; in every
    other test it is noise.
    """
    monkeypatch.setattr(
        supervisor_mod, "probe_for", lambda kind: (lambda port, timeout=5.0: False)
    )


@pytest.fixture
def console(monkeypatch):
    """What the supervisor wrote to the server console, as plain text.

    Rebinds the logging module's ``rich.Console`` objects rather than using
    ``capfd`` or ``capsys``. Both of those swap ``sys.stdout``/``sys.stderr``, but
    those consoles were built at import time with ``file=sys.stderr`` already
    bound to pytest's own global capture — so the supervisor's output lands in a
    stream neither fixture owns. Pointing the consoles at a buffer is explicit
    about what is being asserted and does not depend on which capture was active
    when the module happened to be imported.
    """
    import io

    from rich.console import Console

    from yappy_library.adapters import logging as logging_mod

    buffer = io.StringIO()
    monkeypatch.setattr(logging_mod, "_console", Console(file=buffer, width=10_000))
    monkeypatch.setattr(logging_mod, "_err_console", Console(file=buffer, width=10_000))

    def _read():
        return buffer.getvalue()

    return _read


def _dies_on_nth_check(sup, monkeypatch, n=3):
    """Make a supervisor's liveness check report alive, then dead.

    Three is the real sequence inside ``run``: the up-front check, the first
    abort tick, and the tick that catches the death. A process that dies after
    the up-front check has already passed is exactly what the up-front check
    cannot see, and it is the case these tests are about.
    """
    calls = {"n": 0}

    def flipping():
        calls["n"] += 1
        return calls["n"] < n

    monkeypatch.setattr(sup, "still_running", flipping)


# --- un proceso que muere no es un puerto que no responde ---------------------


def test_a_process_that_is_gone_before_the_probe_is_reported_as_dead(tracker, never_up):
    """La version vieja colapsaba "el probe no contesto" y "el proceso murio" en
    un solo False, y de ahi salia un veredicto que culpaba a un puerto que el
    proceso nunca llego a pedir."""
    _track(local_port=DEAD_PORT)
    _forget()

    ok, died = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http").verdict(timeout=30.0)

    assert ok is False
    assert died is True


def test_a_tracker_entry_is_not_evidence_that_the_process_is_alive(tracker, never_up):
    """La entrada sobrevive al proceso. Siempre.

    Este es el test que hace falta para que el otro no sirva: si la liveness se
    respondiera mirando el registry en vez de preguntar al SO, un proceso que
    murio hace treinta segundos seguiria "vivo" porque su archivo sigue ahi, la
    espera corrieria el timeout completo, y el veredicto volveria a culpar al
    puerto. El registry no es un registro de vida; es un archivo que alguien
    todavia no borro.
    """
    dead_pid = 2147480000  # un pid que no existe
    process_tracker.track_process(
        pid=dead_pid, resource="tunnel", target="t", owner="web",
        state="starting", local_port=DEAD_PORT,
    )
    assert process_tracker.get_tracked_processes(), "el registry deberia tener la entrada"

    sup = Supervisor(pid=dead_pid, port=DEAD_PORT, probe="http")

    assert sup.still_running() is False, "una entrada sin proceso no es un proceso"
    ok, died = sup.verdict(timeout=30.0)
    assert ok is False
    assert died is True


def test_a_process_that_dies_during_the_wait_is_caught_as_dead(tracker, never_up,
                                                              monkeypatch):
    """El caso que el chequeo inicial no puede ver: existia en t=0 y murio
    despues. Sigo siendo una muerte, no un puerto mudo."""
    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http")
    _dies_on_nth_check(sup, monkeypatch)

    ok, died = sup.verdict(timeout=30.0)

    assert ok is False
    assert died is True


def test_a_process_that_lives_and_stays_quiet_is_not_called_dead(tracker, never_up):
    """El caso opuesto: sigue vivo y no contesta. Eso si es un problema de red, y
    el mensaje tiene que seguir hablando de la red."""
    _track(local_port=DEAD_PORT)

    ok, died = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http").verdict(timeout=0.01)

    assert ok is False
    assert died is False


def test_a_wait_ends_as_soon_as_the_process_is_gone(tracker, never_up, monkeypatch):
    """Esperar treinta segundos a algo que ya no existe no es patience: es mentir
    durante treinta segundos. El probe no va a pasar y el proceso no va a
    volver, asi que el loop tiene que salir en el primer tick que lo comprueba."""
    import time

    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http")
    _dies_on_nth_check(sup, monkeypatch)

    started = time.monotonic()
    sup.verdict(timeout=60.0)
    elapsed = time.monotonic() - started

    # El timeout eran 60s. Lo que se mide aqui es que el abort corta la espera
    # en vez de agotarla: dos ticks de POLL_INTERVAL, no un minuto de mentira.
    assert elapsed < 5.0, f"tardo {elapsed:.1f}s en descubrir que el proceso no estaba"


# --- el motivo esta en el log del proceso -------------------------------------


def test_the_verdict_quotes_what_the_process_actually_printed(tracker, tmp_path, never_up,
                                                             monkeypatch, console):
    """La razon de un arranque fallido casi nunca esta en nada que este modulo
    sepa. Esta en una linea del output del proceso, en un archivo que el tracker
    ya conoce."""
    log = tmp_path / "kafka-ui-21480.log"
    log.write_text(
        "arrancando\n"
        "org.yaml.snakeyaml.error.YAMLException: MalformedInputException\n"
        "Application run failed\n",
        encoding="utf-8",
    )
    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http",
                     label="Kafdrop UI", log_file=str(log))
    _dies_on_nth_check(sup, monkeypatch)

    sup.run(timeout=30.0)
    printed = console()

    assert "MalformedInputException" in printed
    assert "Kafdrop UI" in printed


def test_the_verdict_says_died_rather_than_blaming_the_port(tracker, tmp_path, never_up,
                                                           monkeypatch, console):
    """La frase que mandaba a mirar la red cuando no habia red."""
    log = tmp_path / "tunnel-8402.log"
    log.write_text("aws: [ERROR]: Token has expired and refresh failed\n", encoding="utf-8")
    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http",
                     label="personal-profile", log_file=str(log))
    _dies_on_nth_check(sup, monkeypatch)

    sup.run(timeout=30.0)
    printed = console()

    assert "abrio el puerto" not in printed
    assert "murio" in printed
    assert "Token has expired" in printed


def test_brackets_in_the_process_log_survive_to_the_console(tracker, tmp_path, never_up,
                                                            monkeypatch, console):
    """El logger imprime con rich, que trata `[algo]` como markup y borra el
    texto. Un mensaje cuyo unico trabajo es decir que escribio el proceso no
    puede editarar su propia cita."""
    log = tmp_path / "ruido.log"
    log.write_text("arranco con [inventado]ESTO dentro[/inventado]\n", encoding="utf-8")
    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http",
                     label="algo", log_file=str(log))
    _dies_on_nth_check(sup, monkeypatch)

    sup.run(timeout=30.0)

    assert "ESTO dentro" in console()


def test_a_missing_log_does_not_break_the_verdict(tracker, tmp_path):
    """El log puede no existir todavia -- recien spawneado -- y puede estar
    bloqueado por otro proceso. Un reportero no puede morir por no poder leer."""
    _track(local_port=DEAD_PORT)
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http",
                     log_file=str(tmp_path / "no-existe.log"))
    _forget()

    assert sup.log_tail() == []
    sup.run(timeout=30.0)  # no debe levantar


def test_a_log_with_undecodable_bytes_still_gets_read(tracker, tmp_path):
    """Un log de proceso puede tener cualquier cosa. Si no se puede decodificar,
    se pierde la unica linea que explicaba todo."""
    log = tmp_path / "ruido.log"
    log.write_bytes(b"inicio\n\x97\x97\x97 basura\nfin\n")
    _track(local_port=DEAD_PORT)

    tail = Supervisor(
        pid=os.getpid(), port=DEAD_PORT, probe="http", log_file=str(log),
    ).log_tail()

    assert tail, "no debe perder el log por un byte indecodificable"
    assert any("basura" in line for line in tail)


def test_the_quoted_tail_is_ascii(tracker, tmp_path):
    """La consola de Windows es cp1252: un byte multibyte corta la linea, y un
    log cortado no sirve para debuggear."""
    log = tmp_path / "acentos.log"
    log.write_text("todo bien\nfallo con acentos: clever\n", encoding="utf-8")
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http", log_file=str(log))

    quoted = sup.verdict_log()

    assert quoted.isascii(), quoted
    assert "fallo con acentos" in quoted


def test_the_tail_is_bounded(tracker, tmp_path):
    """Estos logs son append-mode y el de un tunel largo no es chico. Un
    supervisor que pega un log entero en la consola dejo de ser un resumen."""
    log = tmp_path / "enorme.log"
    log.write_text("\n".join(f"linea {i}" for i in range(5000)), encoding="utf-8")
    sup = Supervisor(pid=os.getpid(), port=DEAD_PORT, probe="http", log_file=str(log))

    tail = sup.log_tail()

    assert len(tail) <= 3
    assert "linea 4999" in tail[-1]