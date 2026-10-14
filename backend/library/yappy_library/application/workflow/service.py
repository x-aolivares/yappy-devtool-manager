"""Read-only view of what is running: one snapshot per poll.

This is the whole point of the screen, and it is a single call on purpose. The
browser does not ask "is 8402 up, is 8080 up, is the refresher running, is
another cluster holding 8403"; it asks for the screen, and the server resolves
ownership and port conflicts once against every tracked process and every
cluster's catalog. A client that derived those itself would need all of that
data anyway, and would derive them differently on each row.
"""

from __future__ import annotations

import time

from yappy_library.adapters.process_tracker import get_tracked_processes
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
from yappy_library.application.workflow.catalog import entries_for
from yappy_library.application.workflow.models import (
    STATE_DOWN,
    PortRow,
    ResourceRow,
    WorkflowSnapshot,
    is_foreign,
    is_live,
    state_of,
)
from yappy_library.application.workflow.registry import resources_for
from yappy_library.config import Config

#: Swagger UI is what the CLI already opens for a cluster tunnel, so asking for
#: it is asking for the thing the tunnel is for. Any HTTP answer proves the data
#: channel moved bytes; a 404 would too.
CLUSTER_PROBE_PATH = "/swagger-ui/index.html"


def remote_host_for(cfg: Config, cluster: str) -> str:
    """The host a cluster's tunnels land on, for one environment."""
    host = CLUSTER_ALIASES.get(cluster)
    if not host:
        return ""
    domain = cfg.get("CLUSTER_DOMAIN", "yappycloud.com")
    return f"{host}.{cfg.env}.{domain}"


def _holders_by_port(entries: list[dict]) -> dict[int, list[dict]]:
    """Every tracked process per local port, including the duplicates."""
    by_port: dict[int, list[dict]] = {}
    for entry in entries:
        port = entry.get("local_port")
        if port is None:
            continue
        by_port.setdefault(port, []).append(entry)
    return by_port


def _owner(holders: list[dict], cluster: str) -> dict | None:
    """The process that owns this row, for this cluster.

    Ownership is ``(cluster, local_port)``, not ``local_port``. A ``cap2`` tunnel
    on 8403 does not make ``cap``'s 8403 up: that row is exactly the one that
    cannot start while the port is taken, which is what the conflict note is for.
    """
    for entry in holders:
        if (entry.get("cluster") or "") == cluster:
            return entry
    return None


def _conflict_label(holders: list[dict], owner: dict | None) -> str | None:
    """Name the other holder of a port, when one is live and is not the owner."""
    for holder in holders:
        if holder is owner:
            continue
        if not is_live(state_of(holder)):
            continue
        cluster = holder.get("cluster")
        if cluster:
            return f"el cluster {cluster}"
        return f"el recurso {holder.get('target') or holder.get('pid')}"
    return None


def _attributed(entry: dict | None) -> tuple[str, int | None, float | None, bool, str]:
    if entry is None:
        return STATE_DOWN, None, None, False, ""
    return (
        state_of(entry),
        entry.get("pid"),
        entry.get("started_at"),
        is_foreign(entry),
        entry.get("log_file") or "",
    )


def _browser_url(def_port: int | None) -> str | None:
    if not def_port:
        return None
    return f"https://localhost:{def_port}{CLUSTER_PROBE_PATH}"


def snapshot(cfg: Config, cluster: str) -> WorkflowSnapshot:
    """One consistent read of every resource and every catalog row."""
    tracked = get_tracked_processes()
    holders = _holders_by_port(tracked)

    resources: list[ResourceRow] = []
    for definition in resources_for(cfg):
        # A resource is not a cluster tunnel, so it only ever matches a process
        # that was not started for one.
        candidates = holders.get(definition.local_port, []) if definition.local_port else []
        entry = _owner(candidates, "")
        state, pid, since, foreign, log_file = _attributed(entry)
        resources.append(
            ResourceRow(
                key=definition.key,
                group=definition.group,
                name=definition.name,
                detail=definition.detail,
                state=state,
                local_port=definition.local_port,
                pid=pid,
                since=since,
                foreign=foreign,
                browser_url=definition.browser_url,
                log_file=log_file,
                conflict=_conflict_label(candidates, entry),
            )
        )

    ports: list[PortRow] = []
    for catalog_entry in entries_for(cfg, cluster):
        candidates = holders.get(catalog_entry.port, [])
        entry = _owner(candidates, cluster)
        state, pid, since, foreign, log_file = _attributed(entry)
        ports.append(
            PortRow(
                cluster=cluster,
                port=catalog_entry.port,
                label=catalog_entry.label,
                state=state,
                pid=pid,
                since=since,
                foreign=foreign,
                browser_url=_browser_url(catalog_entry.port) if is_live(state) else None,
                log_file=log_file,
                conflict=_conflict_label(candidates, entry),
            )
        )

    # What is running and that no row can account for: tracked before the port
    # field existed. A tunnel on another cluster is not listed here — it is
    # reachable by switching clusters, and it is counted by "Parar todo".
    unattributed = [
        entry for entry in tracked if entry.get("alive") and entry.get("local_port") is None
    ]

    return WorkflowSnapshot(
        env=cfg.env or "",
        cluster=cluster,
        remote_host=remote_host_for(cfg, cluster),
        generated_at=time.time(),
        resources=resources,
        ports=ports,
        unattributed=unattributed,
    )