"""Caso de uso: resolver en Secrets Manager el valor de un parámetro.

El valor crudo del parámetro ES el nombre del secreto. No hay convención de
prefijo ni búsqueda: se toma `config/env.<env>` -> `<PARAM>` y ese string se pasa
 tal cual a `GetSecretValue`.

Solo se ejecuta cuando el usuario lo pide (botón "resolver"). El listado de
parámetros nunca dispara una llamada a AWS.
"""
from __future__ import annotations

from ..domain.entities import Parameter, ResolvedSecret
from ..domain.exceptions import EnvironmentNotFoundError
from ..domain.ports import EnvironmentRepository, ParameterRepository, SecretResolver


def resolve_secret(
    env_repo: EnvironmentRepository,
    param_repo: ParameterRepository,
    resolver: SecretResolver,
    env: str,
    key: str,
) -> ResolvedSecret:
    parameter = _find_parameter(env_repo, param_repo, env, key)
    return resolver.resolve(env, parameter.value)


def _find_parameter(
    env_repo: EnvironmentRepository,
    param_repo: ParameterRepository,
    env: str,
    key: str,
) -> Parameter:
    from ..domain.exceptions import ParameterNotFoundError

    known = [e.name for e in env_repo.list_environments()]
    if env not in known:
        raise EnvironmentNotFoundError(env, known)

    parameters = param_repo.list_parameters(env)
    for p in parameters:
        if p.key == key:
            return p
    raise ParameterNotFoundError(key, env)
