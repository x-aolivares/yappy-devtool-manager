"""The state stream: pushed when it changes, asked for never.

These tests are about the *shape* of the traffic between the backend and the four
workflow screens, and they can only assert that by holding both ends: a real
`_stream_frames` generator, the real tracker writing into a temp dir, and the real
bus waking the generator.

They drive the generator directly instead of through ``TestClient`` because the
stream never ends, so a test client would wait for a response that never completes.
What is under test is the loop, not Starlette's chunking.

What each one pins down, and why it matters:

- **A verdict arrives without anyone asking.** This is the whole feature. A start
  writes the tracker and publishes; the row moves.
- **A publish with no change sends nothing.** The failure mode this project had: a
  request that comes back saying "identical". The stream has to be able to stay
  quiet, and the way to prove it is to publish and assert that no frame came.
- **The watchdog catches what nobody published.** A tunnel opened from the terminal,
  or a process killed by something else, has no event at all. Without the watchdog
  those changes are invisible forever, so it is not a fallback — it is the only way
  they are seen.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yappy_api import app as web_app
from yappy_api import shutdown
from yappy_api.routes import workflow as routes_workflow
from yappy_library.adapters import process_tracker
from yappy_library.adapters.state_bus import StateBus
from yappy_library.config import Config


@pytest.fixture
def bus(monkeypatch):
    """One bus object on **all** sides, for the test's lifetime.

    All halves matter, and getting only one of them right is a silent failure
    rather than an error: the tracker importing the singleton by name means a test
    that swaps the route's bus but not the tracker's publishes to an audience of
    zero, and then times out waiting for a frame that was never addressed to it.
    The shutdown module is the third: ``begin()`` wakes the streams through it, so
    a test where one of them disagrees would never see the wake.
    """
    local = StateBus()
    monkeypatch.setattr(process_tracker, "state_bus", local)
    monkeypatch.setattr(routes_workflow, "state_bus", local)
    monkeypatch.setattr(shutdown, "state_bus", local)
    return local


@pytest.fixture
def watchdog(monkeypatch):
    """A watchdog short enough to be worth waiting for in a test."""

    monkeypatch.setattr(routes_workflow, "STREAM_WATCHDOG_SECONDS", 0.2)


async def _collect(frames, sink: list) -> None:
    async for raw in frames:
        sink.append(json.loads(raw[len("data: ") :]))


async def _until(sink: list, count: int, timeout: float = 3.0) -> bool:
    """Wait for ``count`` frames, without cancelling the stream to find out.

    A timeout that cancels ``__anext__`` throws ``CancelledError`` into the
    generator at its suspension point and ends it, so "did it arrive?" cannot be
    asked that way — asking it that way changes the answer.
    """
    deadline = time.monotonic() + timeout
    while len(sink) < count and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return len(sink) >= count


async def _settle(seconds: float) -> None:
    """Long enough that a frame would have arrived if one was coming."""

    await asyncio.sleep(seconds)


async def _open(cfg: Config, cluster: str = "cap"):
    """Start the stream, collect it in the background, and hand back both."""

    frames = routes_workflow._stream_frames(cfg, cluster)
    sink: list[dict] = []
    pump = asyncio.ensure_future(_collect(frames, sink))
    return frames, sink, pump


async def _close(frames, pump) -> None:
    pump.cancel()
    await asyncio.gather(pump, return_exceptions=True)
    await frames.aclose()


def _run(coro):
    return asyncio.run(coro)


def _track(pid: int) -> None:
    """A tracked entry with no local port, so it lands in ``unattributed``.

    ``unattributed`` is the one list in the snapshot that moves for *any* tracked
    process, whatever port or cluster it names — so a test can make the state change
    without having to know what the local catalog and registry happen to contain
    today. Goes through the public writer, so it publishes.
    """
    process_tracker.track_process(pid, "tunnel", target="cap", owner=process_tracker.OWNER_WEB)


def _track_from_another_process(pid: int) -> None:
    """Write the same file without publishing — what the CLI does.

    ``call_soon_threadsafe`` does not cross a process boundary, so a tunnel opened
    from a terminal reaches this bus no matter how willing the writer is. That
    asymmetry is the entire reason the watchdog exists, and a test that cannot
    produce it cannot test it.
    """
    directory = process_tracker._TRACKER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{pid}.json"
    (directory / name).write_text(
        json.dumps(
            {
                "pid": pid,
                "resource": "tunnel",
                "target": "cap",
                "state": "up",
                "local_port": None,
                "owner": process_tracker.OWNER_CLI,
            }
        )
    )
    # The parse cache keys on mtime+size, and a rewrite inside the same clock tick
    # can match both. Drop it rather than depend on the filesystem's timestamp.
    process_tracker._forget(name)


def _unattributed(body: dict) -> int:
    return len(body["unattributed"])


def test_el_stream_manda_el_estado_completo_al_conectarse(bus):
    """The first frame is a whole snapshot, so a reader has something to draw."""

    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)
        assert len(bus) == 1, "no se suscribió: nadie escucharía el push"
        body = sink[0]
        assert body["env"] == "dev" and body["cluster"] == "cap"
        # The same shape as GET /api/workflow/state: one serializer, two transports.
        assert {"resources", "ports", "unattributed", "remote_host"} <= set(body)
        await _close(frames, pump)

    _run(main())


def test_un_veredicto_llega_sin_que_nadie_pregunte(bus):
    """The point of the feature: the row moves because the backend published.

    A start is a ``202`` and a supervisor thread that can take thirty seconds. The
    old screen answered "not yet" twenty times in a row; this one gets one frame,
    when there is finally something to say.
    """
    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)
        before = _unattributed(sink[-1])

        _track(os.getpid())  # writes the tracker, and publishes

        assert await _until(sink, 2), "el veredicto no llegó"
        assert _unattributed(sink[-1]) == before + 1
        await _close(frames, pump)

    _run(main())


def test_un_publish_sin_cambio_no_manda_nada(bus):
    """The request that comes back saying "identical" is the thing being removed.

    A publish wakes the reader, the reader re-reads, and the state is byte-for-byte
    what it already sent. Sending anyway would put the request back in a different
    costume; staying quiet is the behaviour, so it is asserted directly.
    """
    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)
        before = _unattributed(sink[-1])

        bus.publish()  # honest signal, but nothing moved

        await _settle(0.6)
        assert len(sink) == 1, "mandó un frame sin que cambiara nada"
        assert _unattributed(sink[-1]) == before
        await _close(frames, pump)

    _run(main())


def test_el_watchdog_atrae_lo_que_nadie_publico(bus, watchdog):
    """A tunnel from the terminal has no event. The watchdog is how it shows up.

    This is the half that cannot be pushed: the write came from another process, so
    nothing in this one was in a position to publish. A state change with no publish
    behind it still has to arrive, or a foreign tunnel would sit grey until the
    developer reloaded the page by hand.
    """
    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)
        assert _unattributed(sink[0]) == 0

        _track_from_another_process(os.getpid())

        # The *second* frame, not the second element of a list that already holds the
        # first: waiting for "one more frame" when one is already sitting there would
        # pass on the frame that was already there and prove nothing.
        assert await _until(sink, 2, timeout=3.0), "el watchdog no atropelló el cambio"
        assert _unattributed(sink[1]) == 1
        await _close(frames, pump)

    _run(main())


def test_cerrar_el_stream_deja_de_suscribir(bus):
    """No leak: a dead connection must not keep a queue for the process's life.

    There is no shutdown hook to lean on — the app runs with ``lifespan="off"`` — so
    the only teardown that can be trusted is the generator's ``finally``. If it did
    not run, every navigation away from a workflow page would leak a subscriber.
    """
    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)
        assert len(bus) == 1

        await _close(frames, pump)

        assert len(bus) == 0
        bus.publish()  # and a publish to nobody must not raise
        assert len(bus) == 0

    _run(main())


def test_dos_lectores_no_se_pisan(bus, watchdog):
    """Two open pages, two readers, one change: both are told.

    Each connection carries its own environment and cluster, so a reader cannot be
    handed somebody else's snapshot, and closing one must leave the other connected.
    """
    async def main():
        cfg = Config.with_env("dev")
        frames_a, sink_a, pump_a = await _open(cfg, "cap")
        frames_b, sink_b, pump_b = await _open(cfg, "cap")
        assert await _until(sink_a, 1) and await _until(sink_b, 1)
        assert len(bus) == 2

        _track(os.getpid())

        assert await _until(sink_a, 2) and await _until(sink_b, 2)
        await _close(frames_a, pump_a)
        assert len(bus) == 1, "cerrar uno se llevó al otro"
        await _close(frames_b, pump_b)
        assert len(bus) == 0

    _run(main())


def test_un_ambiente_que_no_existe_es_400_y_no_un_stream_mudo():
    """Validation happens before the response starts, because after it cannot be said.

    A `400` is only expressible while the status line can still be written. Raised
    inside the generator it could not be reported at all: the client would see a
    stream that opens and then says nothing, forever, with no error anywhere.
    """
    client = TestClient(web_app.app)

    response = client.get("/api/workflow/stream", params={"env": "nope"})

    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_un_cluster_que_no_existe_es_400():
    client = TestClient(web_app.app)

    response = client.get("/api/workflow/stream", params={"env": "dev", "cluster": "nope"})

    assert response.status_code == 400
    assert "cap" in response.json()["detail"]


# --------------------------------------------------------------------------
# Sin ambiente: la página de Kafka.
# --------------------------------------------------------------------------


def test_sin_env_el_endpoint_abre_el_stream_en_vez_de_400(bus, watchdog):
    """`env` vacío es *sin ambiente*, no el default del endpoint.

    La página de Kafka llama sin `env` a propósito —es full local— y el default
    era `"dev"`, un ambiente real: en una máquina sin `env.dev` el stream
    contestaba 400 antes de abrirse y la sección no cargaba nunca su estado.

    Se conduce el generador directo y no por ``TestClient``: un stream que no
    termina hace esperar al cliente por una respuesta que nunca completa, así
    que "el stream abre" no se puede preguntar por ahí sin colgar el test.
    """

    async def main():
        # El `_cfg` real, sin ambiente: es el paso que antes levantaba el 400.
        cfg = routes_workflow._cfg("")
        frames, sink, pump = await _open(cfg)
        assert await _until(sink, 1), "el stream sin ambiente no mandó nada"
        body = sink[0]
        assert body["env"] == ""
        # Kafka es lo único que existe sin ambiente: el resto son túneles.
        assert {row["key"] for row in body["resources"]} == {"kafka-server", "kafka-ui"}
        assert body["ports"] == []
        await _close(frames, pump)

    _run(main())


def test_sin_env_el_estado_tampoco_pide_un_ambiente(tmp_path, monkeypatch):
    """El mismo `_cfg` para los dos transportes, así que el `GET` también."""
    from yappy_api.routes import workflow as routes

    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("KAFKA_PATH=C:/kafka\n", encoding="utf-8")

    body = routes.api_workflow_state(env="", cluster="cap")

    assert body["env"] == ""
    assert {row["key"] for row in body["resources"]} == {"kafka-server", "kafka-ui"}


def test_sin_env_el_catalogo_de_puertos_no_se_ofrece(tmp_path, monkeypatch):
    """Sin ambiente no hay cluster contra el que abrir un túnel.

    El catálogo vive en `env.base` y se levantaría con `ssm start-session`; una
    fila ahí sería un botón que no puede funcionar.
    """
    from yappy_api.routes import workflow as routes

    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text(
        'KAFKA_PATH=C:/kafka\nCLUSTER_PORTS=cap=8403:commerce\n', encoding="utf-8"
    )

    body = routes.api_workflow_state(env="", cluster="cap")

    assert body["ports"] == []


# --------------------------------------------------------------------------
# El log empujado: la misma pregunta, sin el poll de 1,5s.
# --------------------------------------------------------------------------


@pytest.fixture
def log_check(monkeypatch):
    """A check interval short enough to be worth waiting for."""

    monkeypatch.setattr(routes_workflow, "LOG_STREAM_CHECK_SECONDS", 0.1)


async def _open_log(bus, pid: int, lines: int = 10, log_file: str | None = None):
    """Open the log stream the way the route does: path resolved up front.

    The route has to resolve it before the response starts (a generator body runs
    after), so the test drives the generator the same way instead of pretending the
    generator could raise.
    """
    path = Path(log_file) if log_file else Path(routes_workflow._tracked_log_entry(pid)[1])
    frames = routes_workflow._stream_log_frames(pid, lines, path)
    sink: list[dict] = []
    pump = asyncio.ensure_future(_collect(frames, sink))
    return frames, sink, pump


def _write_log(path, text: str) -> None:
    # Bytes, not text mode: on Windows text mode would translate every ``\\n`` into
    # ``\\r\\n`` and the assertion would be about line endings instead of about what
    # arrived. Same note as the rest of the tests that write logs.
    with path.open("ab") as handle:
        handle.write(text.encode("utf-8"))


def _track_with_log(pid: int, log_file: str) -> None:
    process_tracker.track_process(
        pid,
        "kafka",
        target="server",
        owner=process_tracker.OWNER_WEB,
        log_file=log_file,
    )


def test_el_log_empieza_con_la_cola_del_archivo(bus, tmp_path, log_check):
    """Lo primero que llega es lo que había, sin pedir nada más.

    La primera lectura pide el final del archivo y el stream se queda ahí como
    cursor. Es el mismo contrato que tenía el poll con ``offset=0``, y es lo que
    evita que un log de tres megabytes abra la pantalla tirando tres megabytes.
    """
    log = tmp_path / "kafka.log"
    log.write_text("\n".join(f"linea {i}" for i in range(100)), encoding="utf-8")
    _track_with_log(os.getpid(), str(log))

    async def main():
        frames, sink, pump = await _open_log(bus, os.getpid(), lines=10)

        assert await _until(sink, 1)
        assert sink[0]["text"].startswith("linea 90")
        assert sink[0]["gone"] is False
        assert sink[0]["offset"] == log.stat().st_size
        await _close(frames, pump)

    _run(main())


def test_el_log_llega_solo_cuando_el_archivo_crece(bus, tmp_path, log_check):
    """El caso que dolía: un proceso que no loguea nada.

    El poll preguntaba cada 1,5s y recibía un offset idéntico. Acá la conexión sigue
    abierta, sigue mirando el archivo, y no manda un solo frame mientras no haya
    algo nuevo.
    """
    log = tmp_path / "kafka.log"
    log.write_text("arrancando\n", encoding="utf-8")
    _track_with_log(os.getpid(), str(log))

    async def main():
        frames, sink, pump = await _open_log(bus, os.getpid())

        assert await _until(sink, 1)
        await _settle(0.8)  # muchos ticks de los 0,1s

        assert len(sink) == 1, "mandó un frame con el archivo igual"

        _write_log(log, "una linea nueva\n")

        assert await _until(sink, 2), "la línea nueva no llegó"
        assert sink[1]["text"] == "una linea nueva\n"
        await _close(frames, pump)

    _run(main())


def test_el_log_manda_solo_lo_nuevo(bus, tmp_path, log_check):
    """Dos vueltas mandan la diferencia, no el archivo entero.

    Es la razón de que el cursor viva en la conexión: con el poll el cliente lo
    guardaba y lo mandaba de vuelta, y un log que crece en 300 líneas se reenviaba
    entero en cada lectura.
    """
    log = tmp_path / "kafka.log"
    log.write_text("primera\n", encoding="utf-8")
    _track_with_log(os.getpid(), str(log))

    async def main():
        frames, sink, pump = await _open_log(bus, os.getpid())

        assert await _until(sink, 1)
        _write_log(log, "segunda\n")
        assert await _until(sink, 2)
        _write_log(log, "tercera\n")
        assert await _until(sink, 3)

        assert [f["text"] for f in sink[1:]] == ["segunda\n", "tercera\n"]
        await _close(frames, pump)

    _run(main())


def test_un_pid_que_sale_del_tracker_cierra_el_stream(bus, tmp_path, log_check):
    """El `gone` ahora lo dice el server, en vez de que el panel lo adivine.

    Antes era un 404 en el siguiente tick: una request más, y un panel que podía
    seguir golpeando durante el tiempo entre el untrack y ese 404.
    """
    log = tmp_path / "kafka.log"
    log.write_text("arrancando\n", encoding="utf-8")
    pid = os.getpid()
    _track_with_log(pid, str(log))

    async def main():
        frames, sink, pump = await _open_log(bus, pid)

        assert await _until(sink, 1)
        assert len(bus) == 1

        process_tracker.untrack_process(pid)  # publica: no espera al tick

        assert await _until(sink, 2), "no avisó que el pid se fue"
        assert sink[-1]["gone"] is True
        await asyncio.gather(pump, return_exceptions=True)
        await frames.aclose()

    _run(main())


def test_el_log_se_desuscribe_al_cerrar(bus, tmp_path, log_check):
    """Sin fuga: un panel cerrado no deja una conexión mirando un archivo."""

    log = tmp_path / "kafka.log"
    log.write_text("x\n", encoding="utf-8")
    _track_with_log(os.getpid(), str(log))

    async def main():
        frames, sink, pump = await _open_log(bus, os.getpid())
        assert await _until(sink, 1)

        await _close(frames, pump)

        assert len(bus) == 0

    _run(main())


def test_el_log_de_un_pid_desconocido_es_404_y_no_un_stream_mudo():
    """Igual que el estado: si se puede decir antes de empezar, se dice antes."""

    client = TestClient(web_app.app)

    response = client.get("/api/workflow/stream/logs", params={"pid": 987654})

    assert response.status_code == 404
    assert "987654" in response.json()["detail"] or "trackeado" in response.json()["detail"]


def test_el_log_sin_pid_es_400():
    client = TestClient(web_app.app)

    response = client.get("/api/workflow/stream/logs")

    assert response.status_code == 400
    assert "pid" in response.json()["detail"]


# --------------------------------------------------------------------------
# El apagado: los streams se cierran solos para que el Ctrl+C salga.
# --------------------------------------------------------------------------


def test_el_apagado_cierra_el_stream_de_estado_y_no_espera_al_watchdog(bus, monkeypatch):
    """El Ctrl+C con la web abierta tiene que devolver la terminal sola.

    Una conexión SSE nunca termina: su generator espera un cambio que puede no
    venir nunca, y el shutdown graceful de uvicorn espera a que **todas** las
    conexiones se cierren. Por eso el apagado se anuncia con el flag que los
    streams miran en cada vuelta — y por eso el watchdog acá está puesto en treinta
    segundos: el stream tiene que cerrarse por el aviso, no por el tick, o el
    problema seguiría entero con un número distinto.
    """
    monkeypatch.setattr(routes_workflow, "STREAM_WATCHDOG_SECONDS", 30.0)

    async def main():
        frames, sink, pump = await _open(Config.with_env("dev"))
        assert await _until(sink, 1)

        shutdown.begin()  # lo que hace uvicorn con la señal, vía handle_exit

        done, _ = await asyncio.wait({pump}, timeout=3.0)
        assert pump in done, "el stream siguió abierto después del apagado"
        await _close(frames, pump)
        assert len(bus) == 0, "el apagado cerró la conexión sin desuscribirse"

    _run(main())


def test_el_apagado_cierra_el_stream_de_logs(bus, tmp_path):
    """El otro stream, misma cuenta: el cierre es de los dos o no es.

    Con el flag ya puesto y el archivo ni siquiera tocado: el generator devuelve
    en su primera vuelta, sin leer nada — que es lo que importa, porque en el
    apagado real no hay tiempo que gastar antes de irse.
    """
    async def main():
        frames = routes_workflow._stream_log_frames(os.getpid(), 10, tmp_path / "nada.log")

        shutdown.begin()

        with pytest.raises(StopAsyncIteration):
            await frames.__anext__()
        assert len(bus) == 0

    _run(main())