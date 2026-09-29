"""Adapter que reusa library.config.Config de solo lectura.

No escribe nunca sobre config/.env.local ni sobre ningún archivo — ese estado
lo gestiona exclusivamente library/db/tunnel.py para el CLI. Ver "Principio
rector: no afectar el DevTool actual" en docs/web-app-plan.md.
"""
from __future__ import annotations

from library.config import Config

from ..domain.entities import Environment
from ..domain.exceptions import EnvironmentNotFoundError


class EnvConfigAdapter:
    """Implementa EnvironmentRepository leyendo config/env.* vía library.config."""

    def list_environments(self) -> list[Environment]:
        environments = []
        for name in Config.known_environments():
            cfg = Config.with_env(name)
            environments.append(
                Environment(
                    name=name,
                    aws_profile=cfg.profile,
                    aws_region=cfg.region,
                )
            )
        return environments

    def get_environment(self, name: str) -> Environment:
        known = Config.known_environments()
        if name not in known:
            raise EnvironmentNotFoundError(name, known)
        cfg = Config.with_env(name)
        return Environment(name=name, aws_profile=cfg.profile, aws_region=cfg.region)
