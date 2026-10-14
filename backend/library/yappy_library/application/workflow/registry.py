"""The resources this tool knows how to manage.

A registry, not configuration: these are the services yappy knows how to open a
tunnel to, and the screen's four panels are a rendering of it. Anything the
*developer* decides — which ports of a cluster matter, what they are called —
lives in the catalog instead, and the two are joined by local port.

Ports that depend on the environment are resolved here rather than stored, so a
definition can never carry a port that belonged to another environment.
"""

from __future__ import annotations

from yappy_library.adapters.kafka.service import _broker_port
from yappy_library.adapters.kafka.service import KAFDROP_UI_PORT
from yappy_library.config import Config
from yappy_library.application.workflow.models import ResourceDef


def _kafdrop_port(cfg: Config, env: str) -> int:
    return int(cfg.get("KAFDROP_PORT", "9000" if env == "qa" else "9001"))


def _databricks_port(cfg: Config, env: str) -> int:
    return int(cfg.get("DATABRICKS_PORT", "4433" if env == "dev" else "4434"))


def _databricks_host(cfg: Config, env: str) -> str:
    return cfg.get("DATABRICKS_HOST", f"yappy-lakehouse-{env}.cloud.databricks.com")


def _kafka_server_port(cfg: Config) -> int:
    from pathlib import Path

    return _broker_port(Path(cfg.kafka_core_path) / "config" / "kraft" / "server.properties")


def resources_for(cfg: Config) -> list[ResourceDef]:
    """Every resource, in the order the screen groups them."""
    env = cfg.env or "dev"
    kafdrop_port = _kafdrop_port(cfg, env)
    databricks_port = _databricks_port(cfg, env)
    broker_port = _kafka_server_port(cfg)

    return [
        ResourceDef(
            key="kafka-server",
            group="kafka",
            name="Kafka Server",
            detail="KRaft · storage en devkit/kafka",
            remote_port=broker_port,
            local_port=broker_port,
            probe="kafka",
        ),
        ResourceDef(
            key="kafka-ui",
            group="kafka",
            name="Kafdrop UI",
            detail="Interfaz web del broker local",
            local_port=KAFDROP_UI_PORT,
            browser_url=f"http://localhost:{KAFDROP_UI_PORT}",
        ),
        ResourceDef(
            key="producer",
            group="named",
            name="Producer",
            detail="Broker del ambiente remoto",
            remote_port=9091,
            local_port=3000,
        ),
        ResourceDef(
            key="kafdrop-remote",
            group="named",
            name="Kafdrop (remoto)",
            detail="Kafdrop del ambiente remoto",
            remote_port=9000,
            local_port=kafdrop_port,
            browser_url=f"http://localhost:{kafdrop_port}/",
        ),
        ResourceDef(
            key="databricks",
            group="named",
            name="Databricks",
            detail="Workspace del ambiente remoto",
            remote_port=443,
            local_port=databricks_port,
            remote_host=_databricks_host(cfg, env),
            browser_url=f"https://localhost:{databricks_port}/",
        ),
        ResourceDef(
            key="bastion",
            group="named",
            name="Bastion",
            detail="Documento distinto: sin remote_host, va a la instancia",
            remote_port=9091,
            local_port=3000,
        ),
        ResourceDef(
            key="db",
            group="db",
            name="Tunnel Aurora",
            detail="Token RDS + port-forward al MySQL",
            remote_port=int(cfg.get("AWS_PORT", "53360")),
            local_port=cfg.db_port,
            probe="mysql",
        ),
    ]