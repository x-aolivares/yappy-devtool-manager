"""The tunnel catalog: which local ports of a cluster are worth forwarding.

It lives in ``CLUSTER_PORTS`` in ``env.base`` because it is keyed by cluster and
not by environment — the local ports are local, whichever AWS environment sits
behind them. Keeping it in config also means the values are reviewable and the
example template ships them, instead of a list hardcoded in a module that a
screen would then draw as if it were data.

Format, one ``cluster=port:label`` group per ``;``::

    cap=8402:core-p2m-m2p,8403:commerce-profile;cap2=8412:personal-profile;int=

An empty label is normal: a port can be forwarded before anyone names it.
"""

from __future__ import annotations

from yappy_library.adapters.ssm.targets import CLUSTER_ALIASES
from yappy_library.application.workflow.models import PortEntry

CONFIG_KEY = "CLUSTER_PORTS"


def _parse_entries(text: str) -> list[PortEntry]:
    entries: list[PortEntry] = []
    seen: set[int] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        port_text, _, label = item.partition(":")
        try:
            port = int(port_text.strip())
        except ValueError:
            # A typo in one item must not cost the developer the rest of the
            # catalog. The bad item is dropped, not the group.
            continue
        if port in seen:
            # One port, one tunnel. Drawing the same port twice would be a lie
            # about what the screen can start.
            continue
        seen.add(port)
        entries.append(PortEntry(port=port, label=label.strip()))
    return entries


def parse_catalog(raw: str | None) -> dict[str, list[PortEntry]]:
    """Parse a ``CLUSTER_PORTS`` value into ``{cluster: [PortEntry, ...]}``."""
    catalog: dict[str, list[PortEntry]] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        cluster, _, entries = chunk.partition("=")
        cluster = cluster.strip()
        if not cluster:
            continue
        catalog[cluster] = _parse_entries(entries)
    return catalog


def catalog_for(cfg) -> dict[str, list[PortEntry]]:
    """The configured catalog, with every known cluster present.

    A cluster with no configuration gets an empty list rather than missing, so
    the screen can tell "this cluster has no ports" apart from "this cluster is
    not a cluster". Those are the empty state and an error, not the same thing.
    """
    catalog = parse_catalog(cfg.get(CONFIG_KEY))
    for cluster in CLUSTER_ALIASES:
        catalog.setdefault(cluster, [])
    return catalog


def entries_for(cfg, cluster: str) -> list[PortEntry]:
    """The catalog of one cluster, in the order it was configured."""
    return catalog_for(cfg).get(cluster, [])


def format_catalog(catalog: dict[str, list[PortEntry]]) -> str:
    """Render a catalog back into a ``CLUSTER_PORTS`` value.

    Round-trips with :func:`parse_catalog`; used by the write path.
    """
    groups = []
    for cluster, entries in catalog.items():
        rendered = ",".join(f"{e.port}:{e.label}" for e in entries)
        groups.append(f"{cluster}={rendered}")
    return ";".join(groups)