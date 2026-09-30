"""Enum centralizado de puertos usados por todo el toolkit (CLI + web).

Cualquier puerto nuevo que se agregue a la web (o a cualquier servicio futuro)
debe declararse aquí para evitar colisiones con lo que ya usa `library/` /
`cli/` (tunnels SSM, DB, Kafka).
"""
from __future__ import annotations

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
