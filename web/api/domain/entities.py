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
