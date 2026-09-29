"""Puertos (interfaces) que la capa de aplicación consume. Implementados en infrastructure/."""
from __future__ import annotations

from typing import Protocol

from .entities import DbObject, Environment, Parameter, QueryResult, ResolvedSecret, Schema


class EnvironmentRepository(Protocol):
    def list_environments(self) -> list[Environment]: ...
    def get_environment(self, name: str) -> Environment: ...


class ParameterRepository(Protocol):
    def list_parameters(self, env: str) -> list[Parameter]: ...


class SecretResolver(Protocol):
    def resolve(self, env: str, secret_name: str) -> ResolvedSecret: ...


class DbRepository(Protocol):
    def list_schemas(self, env: str) -> list[Schema]: ...
    def list_objects(self, env: str, schema: str) -> list[DbObject]: ...
    def run_query(self, env: str, schema: str, sql: str) -> QueryResult: ...


class LocalMysqlController(Protocol):
    def start(self) -> bool: ...
    def is_running(self) -> bool: ...
