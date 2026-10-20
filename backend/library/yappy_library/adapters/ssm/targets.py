"""Reusable aliases for the SSM-forwarded service clusters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - sólo para el tipado
    from yappy_library.config import Config

CLUSTER_ALIASES = {
    "cap": "nlbcapabilities",
    "cap2": "nlbcapabilities.v2",
    "int": "nlbintegration",
}

#: El dominio de los hosts de cluster, cuando la config no dice otro.
#:
#: Vive acá y no repetido en cada lugar que arma un host: la ruta que le
#: reportaba el dominio al browser tenía el literal pegado, así que con un
#: `CLUSTER_DOMAIN` custom la web informaba un dominio y los túneles usaban
#: otro.
DEFAULT_CLUSTER_DOMAIN = "yappycloud.com"


def cluster_domain(cfg: "Config") -> str:
    """El dominio de los hosts de cluster para una config."""
    return cfg.get("CLUSTER_DOMAIN", DEFAULT_CLUSTER_DOMAIN)
