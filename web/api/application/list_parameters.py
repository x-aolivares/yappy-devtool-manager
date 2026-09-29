"""Caso de uso: listar parámetros de un ambiente, con detección JSON vs valor plano.

Nota: en esta primera iteración `ParameterRepository` es un placeholder que lee
únicamente las variables definidas en `config/env.<env>` (vía library.config),
ya que aún no está resuelto de dónde provienen los "parámetros" reales
(SSM Parameter Store vs. otra fuente) — ver preguntas abiertas en
docs/web-app-plan.md. Cuando se confirme la fuente real, este caso de uso no
cambia; solo se reemplaza el adapter en infrastructure/.
"""
from __future__ import annotations

from ..domain.entities import Parameter
from ..domain.exceptions import EnvironmentNotFoundError
from ..domain.ports import EnvironmentRepository, ParameterRepository


def list_parameters(
    env_repo: EnvironmentRepository,
    param_repo: ParameterRepository,
    env: str,
) -> list[Parameter]:
    known = [e.name for e in env_repo.list_environments()]
    if env not in known:
        raise EnvironmentNotFoundError(env, known)
    return param_repo.list_parameters(env)
