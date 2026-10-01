"""Shared FastAPI dependencies for the web API."""

from __future__ import annotations

from fastapi import HTTPException

from ..config import Config


def env_config(env: str) -> Config:
    """Resolve an environment name to its Config, with HTTP-friendly errors."""
    if env not in Config.known_environments():
        raise HTTPException(status_code=400, detail=f"Ambiente desconocido: '{env}'")
    try:
        return Config.with_env(env)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc