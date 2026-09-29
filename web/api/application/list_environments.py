"""Caso de uso: listar los ambientes disponibles."""
from __future__ import annotations

from ..domain.entities import Environment
from ..domain.ports import EnvironmentRepository


def list_environments(repo: EnvironmentRepository) -> list[Environment]:
    return repo.list_environments()


def get_environment(repo: EnvironmentRepository, name: str) -> Environment:
    return repo.get_environment(name)
