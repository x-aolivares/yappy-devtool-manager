"""Composición de dependencias.

Único lugar donde se decide qué adapter concreto implementa cada puerto. Los
routers nunca instancian adapters: reciben el container. Así se puede cambiar
un adapter (p. ej. por un fake en tests) sin tocar la capa de entrada.
"""
from __future__ import annotations

from .infrastructure.aws_secrets_adapter import AwsSecretsAdapter
from .infrastructure.env_config_adapter import EnvConfigAdapter
from .infrastructure.param_config_adapter import EnvFileParameterAdapter


class Container:
    """Holds de adapters concretos. Todos son stateless y seguros de compartir."""

    def __init__(self) -> None:
        self.environments = EnvConfigAdapter()
        self.parameters = EnvFileParameterAdapter()
        self.secrets = AwsSecretsAdapter()


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container


def reset_container() -> None:
    """Solo para tests: fuerza el próximo get_container() a reconstruir."""
    global _container
    _container = None
