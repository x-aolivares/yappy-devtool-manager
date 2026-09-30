"""Entidades de dominio puras — sin dependencias externas (FastAPI, boto3, etc.)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Environment:
    name: str
    aws_profile: str | None = None
    aws_region: str | None = None


@dataclass(frozen=True)
class Parameter:
    key: str
    value: str
    is_json: bool
    environment: str


@dataclass(frozen=True)
class ResolvedSecret:
    secret_name: str
    value: str
    environment: str
    is_json: bool = False


@dataclass(frozen=True)
class Schema:
    name: str
    environment: str


@dataclass(frozen=True)
class DbObject:
    name: str
    kind: str  # "table" | "procedure" | "function"
    schema: str


@dataclass(frozen=True)
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class DbConnectionInfo:
    """Where a connection points, for display in the UI."""

    target: str  # "env:dev" | "local"
    host: str
    port: int
    user: str
    reachable: bool
    detail: str = ""


@dataclass(frozen=True)
class MigrateObject:
    """The DDL of a single database object, ready to be applied locally."""

    kind: str  # "table" | "view" | "procedure" | "function" | "trigger"
    schema: str
    name: str
    ddl: str
    environment: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrateRequest:
    """Migration unit.

    `mode="ddl"` is the only implemented mode today. `tables`, `date_column` and
    `date_from`/`date_to` exist now so the later "data by date range" increment is
    additive and doesn't change this contract.
    """

    environment: str
    schema: str
    mode: str = "ddl"
    objects: tuple[tuple[str, str], ...] = ()  # (kind, name) pairs
    tables: tuple[str, ...] = ()
    date_column: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    target_schema: str | None = None


@dataclass(frozen=True)
class MigrateResult:
    applied: tuple[MigrateObject, ...] = ()
    target_schema: str = ""
    statements_executed: int = 0
    failures: tuple[tuple[str, str], ...] = ()  # (object, error)


@dataclass(frozen=True)
class LocalMysqlStatus:
    running: bool
    host: str
    port: int
    user: str
    detail: str = ""
    start_command: str = ""
