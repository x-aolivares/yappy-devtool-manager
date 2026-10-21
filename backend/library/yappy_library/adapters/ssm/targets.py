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


def cluster_remote_host(cfg: "Config", cluster: str) -> str:
    """El host al que aterriza un túnel de cluster, para un ambiente.

    **Sin ambiente no hay host.** El host de un cluster es
    ``<alias>.<ambiente>.<dominio>``, y un ambiente vacío no es un ambiente: lo
    que salía era ``cap..yappycloud.com``, un nombre que no resuelve a nada.

    Esto estaba escrito tres veces —el servicio del workflow, las acciones y
    la sesión del CLI— con la misma fórmula. Tres copias son tres números que
    pueden divergir, y un túnel que apunta a un host que no es el suyo no
    falla: no trae bytes y la pantalla dice que el túnel está caído.
    """
    host = CLUSTER_ALIASES.get(cluster)
    if not host or not cfg.env:
        return ""
    return f"{host}.{cfg.env}.{cluster_domain(cfg)}"


def kafdrop_port(cfg: "Config", env: str) -> int:
    """El puerto local donde se sirve Kafdrop, para un ambiente.

    La pantalla de recursos y el comando del CLI tienen que usar el mismo: si
    uno decía 9001 y el otro 9000, el túnel del CLI terminaba en un puerto
    donde no hay Kafdrop.
    """
    return int(cfg.get("KAFDROP_PORT", "9000" if env == "qa" else "9001"))


def databricks_port(cfg: "Config", env: str) -> int:
    """El puerto local del túnel a Databricks, para un ambiente."""
    return int(cfg.get("DATABRICKS_PORT", "4433" if env == "dev" else "4434"))


def databricks_host(cfg: "Config", env: str) -> str:
    """El host de Databricks, para un ambiente."""
    return cfg.get("DATABRICKS_HOST", f"yappy-lakehouse-{env}.cloud.databricks.com")
