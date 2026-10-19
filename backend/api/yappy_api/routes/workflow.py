"""Workflow: what is running, and the buttons to change it.

Eight routes for one domain. The reason there are not more is the read side: the
browser polls ``/api/workflow/state`` and gets everything — resources, the
catalog, ownership, port conflicts, the resolved host — in one response. A screen
that asked per row would make N round trips to answer one question it already
had the data for, and would have to re-derive the conflict rules on the client,
which is how two rows end up disagreeing about who holds 8403.

Start is ``202 Accepted`` and returns immediately. The readiness probe runs in a
supervisor thread and writes its verdict to the tracker; the next poll picks it
up. Nothing here waits for a tunnel to come up.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from yappy_library.adapters.kafka.service import KafkaUnavailable
from yappy_library.adapters.process_tracker import get_tracked_processes
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
from yappy_library.adapters.state_bus import state_bus
from yappy_library.application.workflow import actions, catalog, catalog_write
from yappy_library.application.workflow import service as workflow_service
from yappy_library.config import Config

from .. import shutdown
from ..schemas import (
    WorkflowActionResponse,
    WorkflowCatalogAddRequest,
    WorkflowCatalogRenameRequest,
    WorkflowCatalogResponse,
    WorkflowClustersResponse,
    WorkflowPortInfo,
    WorkflowStartPortRequest,
    WorkflowStartResourceRequest,
    WorkflowStateResponse,
    WorkflowStopAllResponse,
    WorkflowStopRequest,
    WorkflowStopResponse,
)

router = APIRouter(tags=["workflow"])

#: How long a connected stream waits before re-reading even if nothing published.
#:
#: Not a safety net for a broken bus: it is how two kinds of change get noticed at
#: all, because **neither of them has an event**. A tunnel opened from the terminal
#: is written by another process, and a process killed by something outside this
#: project writes nothing at all. Both are still true, and the truth is in the OS
#: rather than in the tracker files — so reading it stays a periodic operation.
#:
#: Five seconds because it is the floor of what the screen loses if this gets
#: wrong: a row that moved while nobody was looking, back on its own within one
#: tick. Anything faster is CPU nobody asked for.
STREAM_WATCHDOG_SECONDS = 5.0

#: How often a connected log stream looks at the file.
#:
#: This one is NOT a watchdog in the same sense: it is the only way new bytes get
#: noticed at all, because a child process writing to its own stdout does not tell
#: this server anything. What it replaced was a 1,5s poll from the browser, so half
#: a second is also twice as responsive than what it replaced — for a ``stat()``,
#: which is the whole cost of a turn where the file did not grow.
#:
#: Same interval as ``readiness.POLL_INTERVAL``, and for the same reason: the log of
#: a starting process gets written about as fast as the probe notices it is up.
LOG_STREAM_CHECK_SECONDS = 0.5


def _cfg(env: str) -> Config:
    try:
        return Config.with_env(env)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _cluster(cluster: str) -> str:
    if cluster not in CLUSTER_ALIASES:
        raise HTTPException(
            status_code=400,
            detail=f"'{cluster}' no es un cluster. Usá: {', '.join(CLUSTER_ALIASES)}.",
        )
    return cluster


def _resource_info(row) -> dict:
    return {
        "key": row.key,
        "group": row.group,
        "name": row.name,
        "detail": row.detail,
        "state": row.state,
        "remote_port": row.remote_port,
        "local_port": row.local_port,
        "pid": row.pid,
        "since": row.since,
        "foreign": row.foreign,
        "browser_url": row.browser_url,
        "log_file": row.log_file,
        "conflict": row.conflict,
        "fix": row.fix,
    }


def _port_info(row) -> dict:
    return {
        "cluster": row.cluster,
        "port": row.port,
        "label": row.label,
        "state": row.state,
        "pid": row.pid,
        "since": row.since,
        "foreign": row.foreign,
        "browser_url": row.browser_url,
        "log_file": row.log_file,
        "conflict": row.conflict,
    }


def _unattributed_info(entry: dict) -> dict:
    return {
        "pid": entry.get("pid", 0),
        "resource": entry.get("resource", ""),
        "target": entry.get("target", ""),
        "env": entry.get("env", ""),
        "log_file": entry.get("log_file", ""),
    }


def _state_body(snap) -> dict:
    """The snapshot as the dict the API sends, for both transports.

    One function on purpose: ``GET /api/workflow/state`` and the SSE stream answer
    the same question with the same shape, and two copies of this dict is two places
    where a field gets added to one and forgotten in the other — which reads as "the
    stream is stale", not as "the serializer drifted".
    """
    return {
        "env": snap.env,
        "cluster": snap.cluster,
        "remote_host": snap.remote_host,
        "generated_at": snap.generated_at,
        "catalog_configured": snap.catalog_configured,
        "resources": [_resource_info(r) for r in snap.resources],
        "ports": [_port_info(p) for p in snap.ports],
        "unattributed": [_unattributed_info(e) for e in snap.unattributed],
    }


def _signature(body: dict) -> str:
    """The body as a comparable string, minus the field that always differs.

    ``generated_at`` is the wall clock of the read, so it changes on every pass by
    construction: including it would make every watchdog tick look like a change and
    turn "send only when it moved" into "send constantly". It is still **sent**, so
    the payload a reader gets is the same one ``/api/workflow/state`` returns.
    """
    return json.dumps({k: v for k, v in body.items() if k != "generated_at"})


async def _stream_frames(cfg: Config, cluster: str):
    """Yield one SSE frame per real change, and nothing per non-change.

    The loop is "read, maybe send, then wait for a wake **or** the watchdog". Both
    halves are load-bearing and neither replaces the other: the wake is what makes a
    verdict land in milliseconds instead of up to five seconds, and the watchdog is
    the only way a tunnel started from the terminal ever shows up at all.

    The snapshot goes through :func:`run_in_threadpool` because it is blocking work —
    file reads and, on Windows, a process-table walk — and doing it inline would
    stall every other connection for its duration.
    """
    subscription = state_bus.subscribe()
    last: str | None = None
    try:
        while True:
            if shutdown.is_set():
                # El server se está apagando. Cerrar el stream desde adentro es
                # lo que hace que el Ctrl+C salga limpio: una conexión SSE no
                # termina sola, y el shutdown graceful de uvicorn espera a que
                # todas se cierren — la terminal moría pegada en "Waiting for
                # connections to close" hasta un segundo Ctrl+C. Ver
                # `yappy_api.shutdown`.
                return
            snap = await run_in_threadpool(workflow_service.snapshot, cfg, cluster)
            body = _state_body(snap)
            signature = _signature(body)
            if signature != last:
                last = signature
                yield f"data: {json.dumps(body)}\n\n"
            try:
                await asyncio.wait_for(
                    subscription.queue.get(), STREAM_WATCHDOG_SECONDS
                )
            except TimeoutError:
                # Nothing published. Go around: re-read, and send only if the OS
                # says something moved.
                pass
    finally:
        # Reached when the client disconnects and the generator is closed. Without
        # it the bus would keep a dead connection's queue for the process's life.
        subscription.close()


@router.get(
    "/api/workflow/clusters",
    operation_id="list_workflow_clusters",
    response_model=WorkflowClustersResponse,
)
def api_workflow_clusters():
    return {"clusters": list(CLUSTER_ALIASES), "domain": "yappycloud.com"}


@router.get(
    "/api/workflow/state",
    operation_id="get_workflow_state",
    response_model=WorkflowStateResponse,
)
def api_workflow_state(env: str = "dev", cluster: str = "cap"):
    """Everything one read of the screen needs.

    Kept, and not replaced by the stream: it is the documented shape, the one a
    ``curl`` can hit, and what a reader gets on the first frame.
    """
    cfg = _cfg(env)
    cluster = _cluster(cluster)
    snap = workflow_service.snapshot(cfg, cluster)
    return _state_body(snap)


@router.get(
    "/api/workflow/stream",
    operation_id="stream_workflow_state",
    response_class=StreamingResponse,
)
async def api_workflow_stream(env: str = "dev", cluster: str = "cap"):
    """The same payload, pushed when it changes instead of asked for on a timer.

    The browser opens this once per screen and listens. A start writes the tracker
    and publishes; the stream wakes, re-reads, and sends. The screen that used to ask
    the same question twenty times while a tunnel booted now asks it once.

    **The validation happens here, before the response starts.** An environment or a
    cluster that does not exist is a ``400`` — which is only expressible while the
    status line can still be written. Inside the generator it could not be reported
    at all; the client would see a stream that opens and then says nothing forever.
    """
    cfg = _cfg(env)
    cluster = _cluster(cluster)
    return StreamingResponse(
        _stream_frames(cfg, cluster),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/api/workflow/resources/start",
    operation_id="start_workflow_resource",
    status_code=202,
    response_model=WorkflowActionResponse,
)
def api_workflow_resource_start(req: WorkflowStartResourceRequest, env: str = "dev"):
    """Spawn a resource and supervise it. Returns as soon as the process exists.

    ``202`` and not ``200``: the tunnel is not up yet, and saying so is the whole
    point of the probe running elsewhere.
    """
    cfg = _cfg(env)
    try:
        result = actions.start_resource(cfg, req.key)
    except actions.StartRefused as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    return {"ok": True, **result}


@router.post(
    "/api/workflow/ports/start",
    operation_id="start_workflow_port",
    status_code=202,
    response_model=WorkflowActionResponse,
)
def api_workflow_port_start(req: WorkflowStartPortRequest, env: str = "dev", cluster: str = "cap"):
    cfg = _cfg(env)
    cluster = _cluster(cluster)
    try:
        result = actions.start_port(cfg, cluster, req.port, req.label)
    except actions.StartRefused as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    return {"ok": True, **result}


@router.post(
    "/api/workflow/kafka/clean",
    operation_id="clean_workflow_kafka_storage",
    response_model=WorkflowActionResponse,
)
def api_workflow_kafka_clean():
    """Wipe and re-format the local Kafka storage: the fix for a locked one.

    **This deletes data.** Everything in ``log.dirs`` — every local topic, its
    messages and its configuration — goes, and the storage comes back empty under a
    new cluster id. That is why it is not a row button with no confirmation: the
    screen offers it only when a failed start's own log says this is what would fix
    it, and asks first.

    Reachable from the web because the alternative is worse. The developer who hit
    this failure had exactly one thing that worked — open a terminal and run
    ``yappy run kafka clean`` — and a screen that knows the answer is the wrong
    place to hide it.

    It stops the broker on the way: a live process holds the very files this
    deletes, which is the whole reason the failure happens. ``202``-shaped like the
    other actions, because reformatting takes a couple of seconds.
    """
    from yappy_library.adapters.kafka.service import KafkaService

    try:
        KafkaService().clean()
    except KafkaUnavailable as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    return {"ok": True, "pid": 0, "port": 0, "state": "down", "label": "Kafka storage"}


@router.post(
    "/api/workflow/stop",
    operation_id="stop_workflow_process",
    response_model=WorkflowStopResponse,
)
def api_workflow_stop(req: WorkflowStopRequest):
    try:
        result = actions.stop_entry(req.pid, allow_foreign=req.allow_foreign)
    except actions.StopRefused as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    return {"ok": True, **result}


@router.post(
    "/api/workflow/stop-all",
    operation_id="stop_all_workflow_processes",
    response_model=WorkflowStopAllResponse,
)
def api_workflow_stop_all(allow_foreign: bool = False):
    return {"ok": True, **actions.stop_all(allow_foreign=allow_foreign)}


@router.get(
    "/api/workflow/logs",
    operation_id="read_workflow_logs",
)
def api_workflow_logs(pid: int | None = None, lines: int = 60, offset: int = 0, page: str = ""):
    """The last ``lines`` of a tracked process's log, or what got appended.

    ``offset`` makes this a poll: pass the ``offset`` the previous call returned
    and only new bytes come back. Without it the screen would re-read and
    re-render the whole log every second.

    ``page`` no cambia una sola línea de la respuesta: está porque es lo único
    que el access log de uvicorn imprime de una request que no sea el path, y
    con cuatro secciones usando el mismo endpoint la línea "-GET /logs?pid=20192
    200-" no dice cuál de ellas estaba preguntando. Es diagnóstico, y por eso
    tiene default: un curl sin el parámetro sigue funcionando.

    Queda igual aunque exista :func:`api_workflow_log_stream`: es la forma
    documentada y la que sirve para un ``curl`` o para un script.
    """
    entry, path = _tracked_log_entry(pid)
    return {
        "pid": entry.get("pid"),
        **_log_chunk(path, offset, lines),
        "alive": bool(entry.get("alive")),
    }


def _tracked_log_entry(pid: int | None) -> tuple[dict, Path]:
    """The tracked entry behind ``pid`` and its log file, or the 404 that says why not.

    Shared by the request and the stream so both answer "which file?" the same way.
    The two 404s are different failures with the same consequence for the panel: a
    pid the tracker doesn't know, and a process with no log to read. Both are
    terminal — there is nothing left to ask again.
    """
    entries = get_tracked_processes()
    if pid is not None:
        entries = [e for e in entries if e.get("pid") == pid]
    if not entries:
        raise HTTPException(status_code=404, detail="No hay ningún proceso trackeado con ese pid.")

    entry = entries[0]
    log_file = entry.get("log_file") or ""
    if not log_file:
        raise HTTPException(status_code=404, detail="Ese proceso no tiene archivo de log.")
    return entry, Path(log_file)


def _log_chunk(path: Path, offset: int, lines: int) -> dict:
    """What the file gained since ``offset``, plus the offset to ask from next time.

    ``offset=0`` means "the first time": the tail, and the end of the file as the
    cursor. From there every read is only what was appended, so a growing log does
    not re-send what the reader already has.
    """
    from yappy_library.adapters.log_tail import last_lines, read_from

    if offset > 0:
        text, new_offset = read_from(path, offset)
    else:
        text = last_lines(path, lines)
        new_offset = path.stat().st_size if path.exists() else 0
    return {"text": text, "offset": new_offset}


async def _stream_log_frames(pid: int, lines: int, path: Path):
    """Yield a frame per stretch of new log lines, and one ``gone`` at the end.

    **The cursor lives here, not in the browser.** The poll made every client keep
    its own offset and send it back on each request; that cursor was the only piece
    of state a connection had, so holding it per connection is both simpler and
    exactly what lets the request disappear.

    **A frame goes out when the file grew, and not otherwise.** That is the whole
    point: a poll against a process that is not logging anything is a request per
    interval that comes back saying nothing, and twenty-eight seconds of silence
    used to cost twenty-eight identical lines in the server console.

    The bus here is a shortcut and never the source: no tracker write announces
    that a *child process* wrote to its own stdout. The tick is what finds the new
    bytes; the bus only makes "it was stopped" land at once instead of on the next
    tick.

    Takes the ``path`` already resolved instead of looking it up here: this is a
    generator, so its body does not run until after the response has started, and a
    404 raised from here is unreportable — it becomes "response already started".
    The caller resolves it while the status line can still be written.
    """
    subscription = state_bus.subscribe()
    offset = 0
    try:
        first = True
        while True:
            if shutdown.is_set():
                # Mismo cierre que en `_stream_frames`, y por la misma razón:
                # el apagado no puede esperar a una conexión SSE que no se
                # cierra sola.
                return
            if not [e for e in get_tracked_processes() if e.get("pid") == pid]:
                # El `gone` del panel: el pid salió del tracker, así que no queda nada
                # que preguntar. Un último frame y el stream se cierra.
                gone = {"pid": pid, "text": "", "offset": offset, "gone": True}
                yield f"data: {json.dumps(gone)}\n\n"
                return

            chunk = await run_in_threadpool(_log_chunk, path, offset, lines)
            if first or chunk["text"]:
                offset = chunk["offset"]
                frame = {"pid": pid, **chunk, "gone": False, "alive": True}
                yield f"data: {json.dumps(frame)}\n\n"
            first = False
            try:
                await asyncio.wait_for(subscription.queue.get(), LOG_STREAM_CHECK_SECONDS)
            except TimeoutError:
                pass
    finally:
        subscription.close()


@router.get(
    "/api/workflow/stream/logs",
    operation_id="stream_workflow_logs",
    response_class=StreamingResponse,
)
async def api_workflow_log_stream(pid: int | None = None, lines: int = 60, page: str = ""):
    """The tail of one process's log, then whatever it writes, pushed as it happens.

    Replaces a 1,5s poll whose answer was almost always "nothing new". The panel
    opens one connection, gets the last 60 lines at once, and after that only
    receives frames when the file actually grew.

    An unknown pid still has to be a status code, so — like the state stream — the
    check happens **here**, in the route, not in the generator. A generator's body
    does not run until the response has started, so a 404 raised from there is not a
    404: it is "response already started" and the client sees a stream that opens
    and then says nothing.
    """
    if pid is None:
        raise HTTPException(status_code=400, detail="Falta el pid.")
    _entry, path = _tracked_log_entry(pid)
    return StreamingResponse(
        _stream_log_frames(pid, lines, path),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


# --- catalog ----------------------------------------------------------------


def _catalog_response(cluster: str, ports) -> WorkflowCatalogResponse:
    return WorkflowCatalogResponse(
        cluster=cluster,
        ports=[
            WorkflowPortInfo(cluster=cluster, port=p.port, label=p.label, state="down")
            for p in ports
        ],
    )


@router.get(
    "/api/workflow/catalog",
    operation_id="get_workflow_catalog",
    response_model=WorkflowCatalogResponse,
)
def api_workflow_catalog(cluster: str = "cap"):
    cfg = Config()
    cluster = _cluster(cluster)
    return _catalog_response(cluster, catalog.entries_for(cfg, cluster))


@router.post(
    "/api/workflow/catalog",
    operation_id="add_workflow_catalog_port",
    response_model=WorkflowCatalogResponse,
)
def api_workflow_catalog_add(req: WorkflowCatalogAddRequest, cluster: str = "cap"):
    cfg = Config()
    cluster = _cluster(cluster)
    try:
        ports = catalog_write.add_port(cfg, cluster, req.port, req.label)
    except catalog_write.CatalogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _catalog_response(cluster, ports)


@router.patch(
    "/api/workflow/catalog/{cluster}/{port}",
    operation_id="rename_workflow_catalog_port",
    response_model=WorkflowCatalogResponse,
)
def api_workflow_catalog_rename(cluster: str, port: int, req: WorkflowCatalogRenameRequest):
    cfg = Config()
    cluster = _cluster(cluster)
    try:
        ports = catalog_write.rename_port(cfg, cluster, port, req.label)
    except catalog_write.CatalogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _catalog_response(cluster, ports)


@router.delete(
    "/api/workflow/catalog/{cluster}/{port}",
    operation_id="remove_workflow_catalog_port",
    response_model=WorkflowCatalogResponse,
)
def api_workflow_catalog_remove(cluster: str, port: int):
    cfg = Config()
    cluster = _cluster(cluster)
    try:
        ports = catalog_write.remove_port(cfg, cluster, port)
    except catalog_write.CatalogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _catalog_response(cluster, ports)


@router.post(
    "/api/workflow/catalog/{cluster}/reset",
    operation_id="reset_workflow_catalog",
    response_model=WorkflowCatalogResponse,
)
def api_workflow_catalog_reset(cluster: str):
    cfg = Config()
    cluster = _cluster(cluster)
    try:
        ports = catalog_write.reset_cluster(cfg, cluster, catalog.parse_catalog(cfg.get(catalog.CONFIG_KEY)))
    except catalog_write.CatalogError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _catalog_response(cluster, ports)