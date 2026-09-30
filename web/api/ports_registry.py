"""Enum centralizado de puertos usados por todo el toolkit (CLI + web).

Cualquier puerto nuevo que se agregue a la web (o a cualquier servicio futuro)
debe declararse aquí para evitar colisiones con lo que ya usa `library/` /
`cli/` (tunnels SSM, DB, Kafka).
"""
from __future__ import annotations

import os
from enum import IntEnum


class YappyPort(IntEnum):
    # Web app (nuevo — este incremento)
    WEB_API = 8300
    WEB_UI_DEV = 4300

    # MySQL nativo local (destino de las migraciones desde los ambientes)
    LOCAL_MYSQL = 3306

    # Ya usados por library/ (CLI) — documentados aquí para evitar colisión
    DB_TUNNEL = 8100          # config/env.*.DB_PORT
    AWS_SSM_LOCAL = 53360     # config/env.base.AWS_PORT
    KAFDROP_DEV = 9001        # config/env.dev.KAFDROP_PORT
    KAFDROP_QA = 9000         # config/env.qa.KAFDROP_PORT (default histórico)
    KAFKA_UI_LOCAL = 8080     # library/kafka: Kafdrop UI local
    KAFKA_BROKER_LOCAL = 9092  # library/kafka: broker KRaft local
    DATABRICKS_DEV = 4433     # config/env.dev.DATABRICKS_PORT
    DATABRICKS_OTHER = 4434   # otros ambientes


# --- overrides ------------------------------------------------------------
#
# Windows reserva rangos de puertos para Hyper-V / WSL2 / Docker / VPN, y un
# bind() sobre un puerto reservado falla con WinError 10013 (WSAEACCES), que no
# parece un problema de puertos. Para no editar código, cada puerto de la web
# acepta un override por variable de entorno. El enum sigue siendo la fuente de
# verdad: esto solo corre el valor default.

#: Puerto overrideable de la API (default `YappyPort.WEB_API`).
ENV_WEB_API_PORT = "YAPPY_WEB_API_PORT"
#: Puerto overrideable de la UI (default `YappyPort.WEB_UI_DEV`).
ENV_WEB_UI_PORT = "YAPPY_WEB_UI_PORT"


def _override(var: str) -> int | None:
    """Read a port override from the environment.

    Raises ValueError on a set-but-unusable value: silently ignoring
    `YAPPY_WEB_API_PORT=abc` would bind the default and leave the user staring
    at a port they explicitly asked to change.
    """
    raw = os.environ.get(var, "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"{var}='{raw}' is not a port number") from None
    if not (1024 <= port <= 65535):
        raise ValueError(f"{var}={port} is out of range (1024-65535)")
    return port


def web_api_port() -> int:
    """Port for the FastAPI backend, honouring `YAPPY_WEB_API_PORT`."""
    override = _override(ENV_WEB_API_PORT)
    return override if override is not None else int(YappyPort.WEB_API)


def web_ui_port() -> int:
    """Port for the Angular dev server, honouring `YAPPY_WEB_UI_PORT`."""
    override = _override(ENV_WEB_UI_PORT)
    return override if override is not None else int(YappyPort.WEB_UI_DEV)
