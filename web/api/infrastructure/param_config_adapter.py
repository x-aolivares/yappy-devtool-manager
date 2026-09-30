"""Adapter de ParameterRepository sobre `config/env.*`.

Los "parámetros" de la web son las variables definidas en `config/env.base` +
`config/env.<ambiente>` (misma fuente que usa el CLI), detectando si el valor es
JSON o un valor plano. Solo lectura: nunca escribe en config/.
"""
from __future__ import annotations

import json

from library.config import Config

from ..domain.entities import Parameter
from ..domain.exceptions import ParameterNotFoundError


class EnvFileParameterAdapter:
    """Implementa ParameterRepository sobre los valores de config/env.<env>."""

    def list_parameters(self, env: str) -> list[Parameter]:
        cfg = Config.with_env(env)
        params: list[Parameter] = []
        for key, value in cfg.as_dict().items():
            if value is None:
                continue
            params.append(
                Parameter(
                    key=key,
                    value=value,
                    is_json=self._looks_like_json(value),
                    environment=env,
                )
            )
        return sorted(params, key=lambda p: p.key)

    def get_parameter(self, env: str, key: str) -> Parameter:
        for p in self.list_parameters(env):
            if p.key == key:
                return p
        raise ParameterNotFoundError(key, env)

    @staticmethod
    def _looks_like_json(value: str) -> bool:
        try:
            json.loads(value)
            return True
        except (json.JSONDecodeError, TypeError):
            return False
