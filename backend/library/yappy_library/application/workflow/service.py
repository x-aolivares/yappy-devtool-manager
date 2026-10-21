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
from pathlib import Path

from yappy_library.adapters.process_tracker import get_tracked_processes
from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES, cluster_remote_host
from yappy_library.application.workflow.catalog import entries_for, is_configured
from yappy_library.application.workflow.models import (
    STATE_DOWN,
    PortRow,
    ResourceRow,
    WorkflowSnapshot,
    is_foreign,
    is_live,
    state_of,
)
from yappy_library.application.workflow.readiness import CLUSTER_PROBE_PATH
from yappy_library.application.workflow.registry import resources_for
from yappy_library.config import Config

def remote_host_for(cfg: Config, cluster: str) -> str:
    """The host a cluster's tunnels land on, for one environment.

    **Sin ambiente no hay host.** El host de un cluster es
    ``<alias>.<ambiente>.<dominio>``, y un ambiente vacío no es un ambiente: lo
    que salía era ``cap..yappycloud.com``, un nombre que no resuelve a nada.
    """
    return cluster_remote_host(cfg, cluster)


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


#: El archivo que describe la máquina del developer.
LOCAL_ENV_NAME = "local"

#: La config sin ambiente, cacheada junto con el directorio de donde salió.
#:
#: El directorio va en la cache y no sólo los valores: una prueba que cambia
#: ``Config._config_dir`` tiene que ver *su* config, no la que quedó cacheada de
#: la máquina. Sin esto, el primer test que tocó la config contaminaba a todos
#: los que venían después.
_cached_environmentless: tuple[Path, dict[str, str | None]] | None = None


def _environmentless_config() -> Config:
    """La config de la pantalla que no eligió ambiente, leída una sola vez.

    Es ``env.base`` con ``env.local`` encima cuando ese archivo existe, que es
    exactamente lo que hereda ``Config()`` — el mismo objeto con el que
    ``KafkaService(Config())`` levanta el broker de verdad. Leerlo en cada tick
    del stream sería releer el archivo cada cinco segundos por un puerto que no
    cambia.

    **Por qué no se elige un ambiente.** El default era ``"dev"``, y ese default
    es un ambiente real: en una máquina sin ``env.dev`` —el checkout de cualquiera
    que haya renombrado ese archivo— el stream de la página de Kafka contestaba
    ``400`` antes de abrir la conexión, así que esa sección no cargaba nunca su
    estado. Kafka no es que no tenga ambiente: *no tiene uno*.

    El ambiente del ``Config`` queda vacío a propósito: es lo que distingue esta
    config de las demás en el snapshot (``env: ""``) y en la registry, donde las
    filas que dependen del ambiente no se ofrecen.
    """
    global _cached_environmentless
    config_dir = Config._get_config_dir()
    cached = _cached_environmentless
    if cached is None or cached[0] != config_dir:
        name = LOCAL_ENV_NAME if (config_dir / f"env.{LOCAL_ENV_NAME}").exists() else None
        cached = (config_dir, dict(Config(name)._values))
        _cached_environmentless = cached
    return Config._from_values(dict(cached[1]))


def snapshot(cfg: Config, cluster: str) -> WorkflowSnapshot:
    """One consistent read of every resource and every catalog row."""
    tracked = get_tracked_processes()
    holders = _holders_by_port(tracked)
    # `getattr` for the same reason as the registry: trimmed configs in tests.
    # Las dos condiciones son una sola idea — *no hay AWS*: o el ambiente es
    # local, o no hay ambiente. Kafka sin ámbito entra por la segunda, y es por
    # eso que su snapshot no ofrece el catálogo de puertos.
    is_local = bool(getattr(cfg, "is_local", False)) or not getattr(cfg, "env", None)

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
                remote_port=definition.remote_port,
                local_port=definition.local_port,
                pid=pid,
                since=since,
                foreign=foreign,
                browser_url=definition.browser_url,
                log_file=log_file,
                conflict=_conflict_label(candidates, entry),
                fix=entry.get("fix", "") if entry else "",
            )
        )

    ports: list[PortRow] = []
    # Cluster tunnels are SSM sessions. A local environment inherits
    # `CLUSTER_PORTS` from `env.base`, but there is no AWS to open it against,
    # so the catalog is not offered rather than offered and refused.
    catalog = [] if is_local else entries_for(cfg, cluster)
    for catalog_entry in catalog:
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
        is_local=is_local,
        catalog_configured=is_configured(cfg),
        resources=resources,
        ports=ports,
        unattributed=unattributed,
    )