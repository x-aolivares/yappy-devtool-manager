"""Workflow/Recursos: what is running, and the buttons to change it.

Eight routes for one screen. The reason there are not more is the read side: the
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

from fastapi import APIRouter, HTTPException

from yappy_library.adapters.process_tracker import get_tracked_processes
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
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
    WorkflowStopRequest,
)

router = APIRouter(tags=["workflow"])


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
    """Everything one poll of the screen needs."""
    cfg = _cfg(env)
    cluster = _cluster(cluster)
    snap = workflow_service.snapshot(cfg, cluster)
    return {
        "env": snap.env,
        "cluster": snap.cluster,
        "remote_host": snap.remote_host,
        "generated_at": snap.generated_at,
        "resources": [_resource_info(r) for r in snap.resources],
        "ports": [_port_info(p) for p in snap.ports],
        "unattributed": [_unattributed_info(e) for e in snap.unattributed],
    }


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
    response_model=WorkflowActionResponse,
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
    response_model=WorkflowActionResponse,
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