"""Pydantic request/response models for the Region Sync web API.

These are the API contract: FastAPI serves them in OpenAPI and the browser
client (Angular) generates its types from them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
    object_type: str  # "table" | "procedure"
    schema_name: str = ""
    code: str


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
    key: str
    is_secret: bool
    service: str
    value: str | None = None
    value_type: str | None = None
    ok: bool
    error: str | None = None


class ParamsReadResponse(BaseModel):
    env: str
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