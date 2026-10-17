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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from yappy_library.adapters.process_tracker import get_tracked_processes
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
from yappy_library.adapters.state_bus import state_bus
from yappy_library.application.workflow import actions, catalog, catalog_write
from yappy_library.application.workflow import service as workflow_service
from yappy_library.config import Config

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
def api_workflow_logs(pid: int | None = None, lines: int = 60, offset: int = 0):
    """The last ``lines`` of a tracked process's log, or what got appended.

    ``offset`` makes this a poll: pass the ``offset`` the previous call returned
    and only new bytes come back. Without it the screen would re-read and
    re-render the whole log every second.
    """
    from pathlib import Path

    from yappy_library.adapters.log_tail import last_lines, read_from

    entries = get_tracked_processes()
    if pid is not None:
        entries = [e for e in entries if e.get("pid") == pid]
    if not entries:
        raise HTTPException(status_code=404, detail="No hay ningún proceso trackeado con ese pid.")

    entry = entries[0]
    log_file = entry.get("log_file") or ""
    if not log_file:
        raise HTTPException(status_code=404, detail="Ese proceso no tiene archivo de log.")

    path = Path(log_file)
    if offset > 0:
        text, new_offset = read_from(path, offset)
    else:
        text = last_lines(path, lines)
        new_offset = path.stat().st_size if path.exists() else 0

    return {
        "pid": entry.get("pid"),
        "text": text,
        "offset": new_offset,
        "alive": bool(entry.get("alive")),
    }


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