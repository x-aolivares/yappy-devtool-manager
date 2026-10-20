"""The resources this tool knows how to manage.

A registry, not configuration: these are the services yappy knows how to open a
tunnel to, and the screen's four panels are a rendering of it. Anything the
*developer* decides — which ports of a cluster matter, what they are called —
lives in the catalog instead, and the two are joined by local port.

Ports that depend on the environment are resolved here rather than stored, so a
definition can never carry a port that belonged to another environment.

Two resources may not share a local port. ``yappy run tunnel producer <env>`` and
the bastion forwarder are the same operation — remote 9091 to local 3000, with no
``remote_host`` — so the screen shows the one that is not an alias. A tunnel
opened with either command still lights the same row, because the row is keyed
by the local port and not by the command.
"""

from __future__ import annotations

from yappy_library.adapters.kafka.service import _broker_port
from yappy_library.adapters.kafka.service import KAFDROP_UI_PORT
from yappy_library.config import Config, aws_port_of
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
    """Every resource, in the order the screen groups them.

    A local environment (`Config.is_local`) only gets the two Kafka rows. The
    other four are SSM tunnels or an Aurora token: a local environment has no
    AWS to point them at, so a button there could only fail. It is the same rule
    the params pages follow — a control that cannot work is worse than no
    control — applied where the registry is read, so ``start_resource`` inherits
    the refusal instead of having to re-derive it.
    """
    env = cfg.env or "dev"
    kafdrop_port = _kafdrop_port(cfg, env)
    databricks_port = _databricks_port(cfg, env)
    broker_port = _kafka_server_port(cfg)

    definitions = [
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
            remote_port=aws_port_of(cfg),
            local_port=cfg.db_port,
            probe="mysql",
        ),
    ]

    # Sin ambiente no hay AWS, y esa es la misma razón que en un ambiente local:
    # los cuatro recursos que quedan son túneles SSM o un token de RDS. Se mira
    # `cfg.env` además de `is_local` porque la pantalla sin ámbito —Kafka— llega
    # con la config cruda, que no tiene ambiente que marcar como local. Es el
    # mismo par de condiciones que usa `routes.workflow._cfg`.
    #
    # `getattr` porque varios llamadores pasan un config recortado (los tests de
    # workflow arman uno con `SimpleNamespace`); el `Config` de producción
    # siempre tiene las dos cosas.
    if getattr(cfg, "is_local", False) or not getattr(cfg, "env", None):
        return [definition for definition in definitions if definition.group == "kafka"]
    return definitions