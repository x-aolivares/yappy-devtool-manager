"""Pydantic request/response models for the Region Sync web API.

These are the API contract: FastAPI serves them in OpenAPI and the browser
client (Angular) generates its types from them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict


class DbDiffRequest(BaseModel):
    env_a: str
    env_b: str
    schema_name: str
    object_type: str  # "table" | "procedure"
    object_name: str
    include_deletes: bool = False


class ParamsDiffRequest(BaseModel):
    env_a: str
    env_b: str
    service: str  # "ssm" | "secretsmanager"
    name: str
    include_deletes: bool = False
    with_secret: bool = False


class ApplyParamsRequest(BaseModel):
    env_a: str
    env_b: str
    service: str  # "ssm" | "secretsmanager"
    name: str
    new_value: str
    value_type: str = "String"
    with_secret: bool = False
    new_secret_value: str = ""
    write_secret: bool = False
    write_param: bool = True
    target: str = "a"  # "a" (destino) | "b" (origen)


class ExecuteParamsRequest(BaseModel):
    env_a: str
    env_b: str
    service: str  # "ssm" | "secretsmanager"
    op: str = "update"  # "update" | "delete"
    name: str
    new_value: str = ""
    value_type: str = "String"
    confirm: bool = False
    with_secret: bool = False
    new_secret_value: str = ""
    write_secret: bool = False
    write_param: bool = True
    target: str = "a"  # "a" (destino) | "b" (origen)


class CreateMultiParamsRequest(BaseModel):
    name: str
    value: str = ""
    value_type: str = "String"
    service: str = "ssm"
    secret_name: str = ""
    secret_value: str = ""
    envs: list[str] = []
    create_secret: bool = False
    dry_run: bool = False
    confirm: bool = False


class ExecuteRequest(BaseModel):
    env: str
    object_type: str  # "table" | "procedure" | "script" (SQL libre, sin objeto único)
    schema_name: str = ""
    code: str


class CompileRequest(BaseModel):
    """Compile an object from one environment into another (origen -> destino)."""

    env_b: str  # origen
    env_a: str  # destino
    object_type: str  # "table" | "procedure"
    schema_name: str
    object_name: str


class CompileResponse(BaseModel):
    """The generated script. Compiling never writes: the caller decides whether
    and when to send ``script`` to ``/api/execute/sql``."""

    env_b: str
    env_a: str
    object_type: str
    schema_name: str
    object_name: str
    status: str
    code_a: str | None = None
    code_b: str | None = None
    script: str | None = None
    notes: list[str] = []


class SchemaCompileRequest(BaseModel):
    """Compile a whole schema from one environment into another (origen -> destino).

    The bulk sibling of ``CompileRequest``: one script for every table and stored
    procedure the origin has in that schema, ordered so MySQL accepts it.

    The scope is one of three shapes, in this order:

    1. ``tables``/``procedures`` carry an explicit list, because the browser
       showed the user a table with a checkbox per object and they chose. An
       explicit list is the whole scope: the two flags below are ignored, since
       they would only widen or narrow what the user just picked.
    2. Otherwise the two flags apply, and both default to ``True`` because "sync
       this schema" means the schema. They exist for a client with no table to
       show, and at least one of them must be on.
    3. An explicit list that is empty is a scope of nothing, which the route
       rejects — it is different from "not given", and reading it as "everything"
       would compile the whole schema the user had just emptied on purpose.
    """

    env_b: str  # origen
    env_a: str  # destino
    schema_name: str
    include_tables: bool = True
    include_procedures: bool = True
    #: The objects the user marked, when the request comes from the schema-sync
    #: page. Empty means "not given": fall back to the flags.
    tables: list[str] | None = None
    procedures: list[str] | None = None


class SchemaCompileResponse(BaseModel):
    """One script for the whole schema. Compiling never writes: the caller decides
    whether and when to send ``script`` to ``/api/execute/sql``.

    ``left_alone`` is the answer to "what happens to the tables that are only in
    the destination": nothing. They are reported so the user sees them before
    running the script, and they never appear in it.
    """

    env_b: str
    env_a: str
    schema_name: str
    status: str
    create_schema: bool
    tables: list[str] = []
    procedures: list[str] = []
    left_alone: list[str] = []
    script: str
    notes: list[str] = []


class QueryRequest(BaseModel):
    env: str
    code: str
    limit: int = 500


class QueryResponse(BaseModel):
    env: str
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    total: int | None = None
    truncated: bool = False
    ms: float = 0.0


class MigrationRequest(BaseModel):
    env_b: str  # origen: de dónde se lee
    env_a: str  # destino: a dónde se escribe
    code: str
    default_schema: str = ""
    dry_run: bool = False
    confirm: bool = False


class MigrationTableInfo(BaseModel):
    """One table of a migration.

    The source table is reported as ``schema``/``table`` rather than
    ``schema_name``/``table_name`` to keep it aligned with the SQL projection it
    came from.
    """

    model_config = ConfigDict(protected_namespaces=())

    schema_name: str
    table_name: str
    alias: str
    target_schema: str
    target_table: str
    select_sql: str
    row_count: int = 0
    replaced: int = 0
    skipped_columns: list[str] = []
    ok: bool = True
    error: str | None = None


class MigrationResponse(BaseModel):
    env_b: str
    env_a: str
    dry_run: bool
    tables: list[MigrationTableInfo] = []
    notes: list[str] = []
    ok_count: int = 0
    err_count: int = 0


class TableSelectionRequest(BaseModel):
    """One table of a table-list migration and the window to take from it.

    ``date_from``/``date_to`` are inclusive dates, as the person filling the form
    reads them; the half-open bounds they are rendered as are the library's
    business. A ``date_column`` with no dates migrates the whole table, which is
    also what omitting the column does.
    """

    table: str
    date_column: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class TableMigrateRequest(BaseModel):
    """Migrate an explicit list of tables of one schema (origen -> destino).

    The bulk sibling of ``MigrationRequest``: same engine, same response model, and
    the source of each ``SELECT`` is this list instead of a user-written query.
    """

    env_b: str  # origen: de dónde se lee
    env_a: str  # destino: a dónde se escribe
    schema_name: str
    tables: list[TableSelectionRequest]
    dry_run: bool = False
    confirm: bool = False


class DateColumnInfo(BaseModel):
    name: str
    type: str


class DateColumnsResponse(BaseModel):
    """The columns a window can be built on, so the picker is not guesswork."""

    env: str
    schema_name: str
    table_name: str
    columns: list[DateColumnInfo] = []


class ReadParamsEntry(BaseModel):
    key: str = ""
    name: str = ""
    is_secret: bool = False


class CreateSessionRequest(BaseModel):
    env_a: str
    env_b: str
    service: str = "ssm"
    keys: list[str] = []
    title: str = ""
    alias: str = ""
    reuse: bool = False


class UpdateSessionItemRequest(BaseModel):
    name: str
    status: str | None = None
    service: str | None = None
    is_secret: bool | None = None
    diff_json: str | None = None
    diff_err: str | None = None
    script: str | None = None
    preview: str | None = None
    notes: str | None = None


class EnvironmentInfo(BaseModel):
    env: str
    region: str | None = None
    profile: str | None = None
    load_error: str | None = None
    """DB_MODE=local: reached directly, no AWS. The client hides it from AWS pages."""
    is_local: bool = False


class EnvironmentsResponse(BaseModel):
    config_dir: str
    override_set: bool
    environments: list[EnvironmentInfo]


class DiffResponse(BaseModel):
    env_a: str
    env_b: str
    object_type: str
    schema_name: str
    object_name: str
    status: str
    code_a: str | None = None
    code_b: str | None = None
    script: str | None = None
    notes: list[str] = []


class StatementResultInfo(BaseModel):
    index: int
    sql: str
    ok: bool
    affected: int | None = None
    ms: float = 0.0
    error: str | None = None


class ExecuteSqlResponse(BaseModel):
    env: str
    object_type: str
    schema_name: str
    results: list[StatementResultInfo] = []
    ok_count: int
    err_count: int


class SchemasResponse(BaseModel):
    env: str
    schemas: list[str] = []


class DbObjectsResponse(BaseModel):
    """The tables or stored procedures of one schema, to feed the object picker.

    Named ``schema_name`` like every other model of this contract: ``schema``
    would shadow the deprecated ``BaseModel.schema`` and warn on import.
    """

    env: str
    schema_name: str
    object_type: str
    objects: list[str] = []


class ParamsDiffResponse(BaseModel):
    env_a: str
    env_b: str
    service: str
    name: str
    status: str
    value_a: str | None = None
    value_b: str | None = None
    value_type_a: str | None = None
    value_type_b: str | None = None
    is_json: bool = False
    changes: list[dict[str, Any]] = []
    patch_value: str | None = None
    script: str | None = None
    notes: list[str] = []
    pair: bool = False
    secret_value_a: str | None = None
    secret_value_b: str | None = None
    secret_changes: list[dict[str, Any]] = []
    secret_patch_value: str | None = None
    param_apply: str | None = None
    secret_apply: str | None = None
    param_status: str = ""
    secret_status: str = ""
    param_needs_write: bool = False
    secret_needs_write: bool = False
    steps: list[dict[str, Any]] = []


class StepCommandInfo(BaseModel):
    step: str
    command: str


class ParamsApplyResponse(BaseModel):
    env_a: str
    env_b: str
    service: str
    name: str
    script: str
    steps: list[StepCommandInfo] = []


class StepResultInfo(BaseModel):
    step: str
    message: str


class ExecuteParamsResponse(BaseModel):
    ok: bool
    message: str
    env_a: str
    env_b: str
    service: str
    op: str
    name: str
    steps: list[StepResultInfo] = []


class MultiResultInfo(BaseModel):
    env: str
    ok: bool
    error: str | None = None
    script: str | None = None
    message: str | None = None


class ParamsMultiResponse(BaseModel):
    name: str
    value_type: str
    create_secret: bool
    dry_run: bool
    results: list[MultiResultInfo]
    ok_count: int
    err_count: int


class ParameterReadInfo(BaseModel):
    env: str
    name: str
    value: str
    value_type: str


class ReadEntryResultInfo(BaseModel):
    env: str = ""
    key: str
    is_secret: bool
    service: str
    value: str | None = None
    value_type: str | None = None
    ok: bool
    error: str | None = None


class ParamsReadResponse(BaseModel):
    envs: list[str] = []
    results: list[ReadEntryResultInfo]
    ok_count: int
    err_count: int


class SessionSummaryInfo(BaseModel):
    id: str
    title: str
    created_at: str
    env_a: str
    env_b: str
    service: str
    item_count: int
    status_counts: dict[str, int]


class SessionItemInfo(BaseModel):
    name: str
    position: int
    service: str
    is_secret: bool
    status: str
    diff_json: str | None = None
    diff_err: str | None = None
    script: str | None = None
    preview: str | None = None
    notes: str | None = None
    visited_at: str | None = None
    applied_at: str | None = None
    updated_at: str | None = None


class SessionDetailResponse(BaseModel):
    id: str
    title: str
    created_at: str
    env_a: str
    env_b: str
    service: str
    status_counts: dict[str, int]
    items: list[SessionItemInfo]


class SessionsListResponse(BaseModel):
    sessions: list[SessionSummaryInfo]


class DeleteResponse(BaseModel):
    ok: bool