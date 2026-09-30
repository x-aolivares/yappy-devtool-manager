"""DTOs Pydantic — capa de serialización HTTP, separada del dominio."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: `schema` is the right name for the domain and for the JSON contract, but it
#: shadows `BaseModel.schema()` and Pydantic warns on every import. The field is
#: therefore declared as `db_schema` with a `schema` alias, so requests and
#: responses still speak plain `"schema"`.
_SCHEMA = Field(alias="schema")


class _HasSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class EnvironmentDTO(BaseModel):
    name: str
    aws_profile: str | None = None
    aws_region: str | None = None


class ParameterDTO(BaseModel):
    key: str
    value: str
    is_json: bool
    environment: str


class ResolvedSecretDTO(BaseModel):
    secret_name: str
    value: str
    is_json: bool
    environment: str


class ResolveSecretRequest(BaseModel):
    key: str


class SchemaDTO(BaseModel):
    name: str
    environment: str


class DbObjectDTO(_HasSchema):
    name: str
    kind: str
    db_schema: str = _SCHEMA


class QueryResultDTO(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: int = 0
    truncated: bool = False


class RunQueryRequest(_HasSchema):
    sql: str
    db_schema: str | None = Field(default=None, alias="schema")
    target: str = Field(default="env", description="'env' or 'local'")


class ConnectionInfoDTO(BaseModel):
    target: str
    host: str
    port: int
    user: str
    reachable: bool
    detail: str = ""


class LocalMysqlStatusDTO(BaseModel):
    running: bool
    host: str
    port: int
    user: str
    detail: str = ""
    start_command: str = ""


class MigrateObjectDTO(_HasSchema):
    kind: str
    db_schema: str = _SCHEMA
    name: str
    ddl: str
    environment: str = ""
    warnings: list[str] = Field(default_factory=list)


class MigrateRequestDTO(_HasSchema):
    db_schema: str = _SCHEMA
    mode: str = "ddl"
    objects: list[tuple[str, str]] = Field(
        default_factory=list,
        description="(kind, name) pairs, e.g. [['table','orders']]",
    )
    tables: list[str] = Field(
        default_factory=list, description="Shorthand: base tables by name"
    )
    date_column: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    target_schema: str | None = None


class MigrateResultDTO(BaseModel):
    target_schema: str
    statements_executed: int
    statements_failed: int
    applied: list[MigrateObjectDTO] = Field(default_factory=list)
    failures: list[tuple[str, str]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: str | None = None
