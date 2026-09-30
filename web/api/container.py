"""Composición de dependencias.

Único lugar donde se decide qué adapter concreto implementa cada puerto. Los
routers nunca instancian adapters: reciben el container. Así se puede cambiar
un adapter (p. ej. por un fake en tests) sin tocar la capa de entrada.
"""
from __future__ import annotations

from .infrastructure.aws_secrets_adapter import AwsSecretsAdapter
from .infrastructure.env_config_adapter import EnvConfigAdapter
from .infrastructure.local_mysql_service import LocalMysqlService
from .infrastructure.mysql_adapter import MysqlAdapter
from .infrastructure.mysql_connection import RdsTokenProvider
from .infrastructure.param_config_adapter import EnvFileParameterAdapter


class Container:
    """Holds of adapters concretos.

    `RdsTokenProvider` is shared on purpose: one token cache across all
    endpoints instead of a fresh IAM call per request.
    """

    def __init__(self) -> None:
        self.environments = EnvConfigAdapter()
        self.parameters = EnvFileParameterAdapter()
        self.secrets = AwsSecretsAdapter()
        self.tokens = RdsTokenProvider()
        self.mysql = MysqlAdapter(self.tokens)
        self.migrate = self.mysql
        self.local_mysql = LocalMysqlService()


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
