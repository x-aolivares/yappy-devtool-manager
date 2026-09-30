"""DTOs Pydantic — capa de serialización HTTP, separada del dominio."""
from __future__ import annotations

from pydantic import BaseModel


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


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: str | None = None
