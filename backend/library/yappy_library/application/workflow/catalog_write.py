"""Changing the catalog: add a port, rename it, remove it.

The screen edits a service name on every keystroke and saves. That is one write
per keystroke against a config file that is also the project's own record, so
this module is deliberately narrow about what it touches:

- **It rewrites one key in one file.** Never a whole `env.base` regeneration,
  which would reformat comments and reorder everything a developer wrote by
  hand.
- **Comments and blank lines survive.** The file is read as text and the
  ``CLUSTER_PORTS=`` line is replaced in place, so the block of Spanish
  explaining the format stays where it was.
- **Validation happens before the write**, so a rejected edit leaves the file
  exactly as it was.
"""

from __future__ import annotations

import re

from yappy_library.application.workflow.catalog import CONFIG_KEY, format_catalog, parse_catalog
from yappy_library.application.workflow.models import PortEntry
from yappy_library.config import Config

#: Ports are 1..65535. A row outside that is not a port, and `ssm start-session`
#: would reject it only after the user already typed it into their catalog.
MIN_PORT = 1
MAX_PORT = 65535

#: Matches a whole ``CLUSTER_PORTS=...`` line, value included. Matching only the
#: ``CLUSTER_PORTS=`` prefix would leave the old value glued onto the new one.
_KEY_LINE = re.compile(rf"^[ \t]*(?:export[ \t]+)?{CONFIG_KEY}[ \t]*=.*$", re.MULTILINE)


class CatalogError(ValueError):
    """A catalog edit that will not be applied, and why."""


def validate_port(port: int) -> None:
    if not isinstance(port, int) or not MIN_PORT <= port <= MAX_PORT:
        raise CatalogError(f"Tiene que ser un puerto entre {MIN_PORT} y {MAX_PORT}.")


def _entries_for(catalog: dict, cluster: str) -> list[PortEntry]:
    return catalog.get(cluster, [])


def add_port(cfg: Config, cluster: str, port: int, label: str = "") -> list[PortEntry]:
    """Add a port to a cluster's catalog and persist it.

    Refuses a port the cluster already has: the screen draws one row per entry,
    and a second row for the same port would be a lie about what it can start.
    """
    validate_port(port)
    catalog = parse_catalog(cfg.get(CONFIG_KEY))
    entries = list(_entries_for(catalog, cluster))
    if any(entry.port == port for entry in entries):
        raise CatalogError(f"El {port} ya está en el catálogo de {cluster}.")
    entries.append(PortEntry(port=port, label=label.strip()))
    catalog[cluster] = entries
    _persist(cfg, catalog)
    return entries


def rename_port(cfg: Config, cluster: str, port: int, label: str) -> list[PortEntry]:
    """Rename a service, keeping the port. An empty label is allowed."""
    validate_port(port)
    catalog = parse_catalog(cfg.get(CONFIG_KEY))
    entries = list(_entries_for(catalog, cluster))
    for index, entry in enumerate(entries):
        if entry.port == port:
            entries[index] = PortEntry(port=entry.port, label=label.strip())
            catalog[cluster] = entries
            _persist(cfg, catalog)
            return entries
    raise CatalogError(f"El {port} no está en el catálogo de {cluster}.")


def remove_port(cfg: Config, cluster: str, port: int) -> list[PortEntry]:
    """Remove a port from the catalog.

    Refuses while the port is held by a live tunnel, because the row the
    developer is deleting is the only thing that can stop it. The screen stops
    the tunnel first and then removes the row; this is the check that makes that
    order the only one that works.
    """
    from yappy_library.adapters.process_tracker import get_tracked_processes

    for entry in get_tracked_processes():
        if entry.get("local_port") == port and entry.get("alive"):
            holder = entry.get("cluster") or entry.get("target") or entry.get("pid")
            raise CatalogError(
                f"El puerto {port} lo está usando {holder}. Paralo antes de quitarlo del catálogo."
            )

    validate_port(port)
    catalog = parse_catalog(cfg.get(CONFIG_KEY))
    entries = list(_entries_for(catalog, cluster))
    remaining = [entry for entry in entries if entry.port != port]
    if len(remaining) == len(entries):
        raise CatalogError(f"El {port} no está en el catálogo de {cluster}.")
    catalog[cluster] = remaining
    _persist(cfg, catalog)
    return remaining


def reset_cluster(cfg: Config, cluster: str, defaults: dict[str, list[PortEntry]]) -> list[PortEntry]:
    """Put a cluster's catalog back to the configured defaults.

    Only for a cluster whose ports are not running: this is the one edit that
    discards developer names wholesale.
    """
    from yappy_library.adapters.process_tracker import get_tracked_processes

    for entry in get_tracked_processes():
        if entry.get("cluster") == cluster and entry.get("alive"):
            raise CatalogError(
                f"Pará los túneles de {cluster} antes de restaurar el catálogo."
            )

    catalog = parse_catalog(cfg.get(CONFIG_KEY))
    catalog[cluster] = list(defaults.get(cluster, []))
    _persist(cfg, catalog)
    return catalog[cluster]


def config_file(cfg: Config):
    """Where ``CLUSTER_PORTS`` is written: ``env.base``.

    The key lives in the shared file because the catalog is keyed by cluster and
    not by environment, and per-environment copies of the same list would be one
    more thing to keep in sync.
    """
    return Config._get_config_dir() / "env.base"


def _persist(cfg: Config, catalog: dict) -> None:
    """Replace the ``CLUSTER_PORTS`` line, or append one, keeping everything else."""
    path = config_file(cfg)
    rendered = format_catalog(catalog)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"No se pudo leer {path}: {exc}") from exc

    if _KEY_LINE.search(text):
        updated = _KEY_LINE.sub(f"{CONFIG_KEY}={rendered}", text, count=1)
    else:
        separator = "" if text.endswith("\n") or not text else "\n"
        updated = f"{text}{separator}{CONFIG_KEY}={rendered}\n"

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"No se pudo escribir {path}: {exc}") from exc