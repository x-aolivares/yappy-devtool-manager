"""Puertos (interfaces) que la capa de aplicación consume. Implementados en infrastructure/."""
from __future__ import annotations

from typing import Protocol

from .entities import (
    DbConnectionInfo,
    DbObject,
    Environment,
    LocalMysqlStatus,
    MigrateObject,
    MigrateResult,
    Parameter,
    QueryResult,
    ResolvedSecret,
    Schema,
)


class EnvironmentRepository(Protocol):
    def list_environments(self) -> list[Environment]: ...
    def get_environment(self, name: str) -> Environment: ...


class ParameterRepository(Protocol):
    def list_parameters(self, env: str) -> list[Parameter]: ...
    def get_parameter(self, env: str, key: str) -> Parameter: ...


class SecretResolver(Protocol):
    def resolve(self, env: str, secret_name: str) -> ResolvedSecret: ...


class DbRepository(Protocol):
    def list_schemas(self, env: str) -> list[Schema]: ...
    def list_objects(self, env: str, schema: str) -> list[DbObject]: ...
    def run_query(self, env: str, schema: str, sql: str) -> QueryResult: ...


class LocalMysqlController(Protocol):
    def start(self) -> bool: ...
    def is_running(self) -> bool: ...


class MigrateRepository(Protocol):
    """Reads DDL out of an environment and writes it into local MySQL."""

    def extract_ddl(
        self, env: str, schema: str, objects: tuple[tuple[str, str], ...]
    ) -> list[MigrateObject]: ...
    def apply_ddl(
        self, ddl_objects: list[MigrateObject], target_schema: str
    ) -> MigrateResult: ...


class DbProbe(Protocol):
    """Liveness probe for a tunneled environment database."""

    def probe(self, env: str) -> DbConnectionInfo: ...


class LocalMysqlService(Protocol):
    def status(self) -> LocalMysqlStatus: ...
    def start(self) -> LocalMysqlStatus: ...
    def stop(self) -> LocalMysqlStatus: ...
