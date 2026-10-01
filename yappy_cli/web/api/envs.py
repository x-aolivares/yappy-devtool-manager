"""Environment metadata router."""

from __future__ import annotations

import os

from fastapi import APIRouter

from ...config import Config
from ..schemas import EnvironmentsResponse

router = APIRouter(tags=["envs"])


@router.get("/api/envs", operation_id="list_environments", response_model=EnvironmentsResponse)
def api_envs():
    config_dir = Config._get_config_dir()
    found = Config.known_environments()
    environments = []
    for env in found:
        entry = {"env": env, "region": None, "profile": None, "load_error": None}
        try:
            cfg = Config.with_env(env)
            entry["region"] = cfg.region
            entry["profile"] = cfg.profile
        except Exception as exc:
            # Never drop an environment: keep it visible and explain the failure.
            entry["load_error"] = str(exc)
        environments.append(entry)
    return {
        "config_dir": str(config_dir),
        "override_set": bool(os.environ.get("YAPPY_CONFIG_DIR")),
        "environments": environments,
    }