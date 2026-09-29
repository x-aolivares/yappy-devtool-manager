"""Adapter placeholder para ParameterRepository.

Lee las variables definidas en config/env.<env> como "parámetros", detectando
si el valor es JSON o un valor plano. Es un placeholder hasta confirmar la
fuente real de parámetros (SSM Parameter Store u otra) — ver preguntas
abiertas en docs/web-app-plan.md.
"""
from __future__ import annotations

import json

from library.config import Config

from ..domain.entities import Parameter


class EnvFileParameterAdapter:
    """Implementa ParameterRepository sobre los valores de config/env.<env>."""

    def list_parameters(self, env: str) -> list[Parameter]:
        cfg = Config.with_env(env)
        params: list[Parameter] = []
        for key, value in cfg.as_dict().items():
            if value is None:
                continue
            is_json = self._looks_like_json(value)
            params.append(
                Parameter(key=key, value=value, is_json=is_json, environment=env)
            )
        return sorted(params, key=lambda p: p.key)

    @staticmethod
    def _looks_like_json(value: str) -> bool:
        try:
            json.loads(value)
            return True
        except (json.JSONDecodeError, TypeError):
            return False
