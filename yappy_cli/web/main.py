"""FastAPI application assembly for the Region Sync web UI.

The project uses Angular as the only active web frontend; the legacy vanilla
JS pages are intentionally not served anymore.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .api import db, envs, params, sessions

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "browser"


def _serve_spa(app: FastAPI) -> None:
    """Serve the compiled Angular app. Registered last so /api and docs win."""
    index = FRONTEND_DIST / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))


def _serve_unavailable(app: FastAPI) -> None:
    """Fallback for environments without an Angular build."""

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def not_ready(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        raise HTTPException(
            status_code=503,
            detail="The Angular frontend build is not available. Run `yappy web --build` or `npm run build` in frontend/.",
        )


def _create_app() -> FastAPI:
    app = FastAPI(
        title="Yappy Region Sync",
        description="Diff de DB (tablas/SP) y de parámetros SSM/Secrets entre regiones de origen y destino",
        version="2.0.0",
    )
    app.include_router(envs.router)
    app.include_router(db.router)
    app.include_router(params.router)
    app.include_router(sessions.router)
    if FRONTEND_DIST.is_dir():
        _serve_spa(app)
    else:
        _serve_unavailable(app)
    return app


app = _create_app()