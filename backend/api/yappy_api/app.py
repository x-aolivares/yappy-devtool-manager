"""FastAPI application assembly for the Region Sync web UI.

The project uses Angular as the only active web frontend; the legacy vanilla
JS pages are intentionally not served anymore.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from yappy_library.paths import project_root

from .routes import db, deployments, envs, params, sessions, workflow

#: Los assets compilados por Angular.
#:
#: Dónde se busca el build es una elección, y esta es la que permite correr los
#: tests sin él. Antes era sólo `<repo>/frontend/dist/browser`: en un checkout de
#: desarrollo ese directorio existe en cuanto alguien compila una vez, y desde
#: ese momento toda la suite —`TestClient` incluido— pasa a ser servida por la
#: app de Angular en vez de por su propia API. Un `POST` a un endpoint real
#: contesta el `index.html` con un 405, que se lee como un endpoint roto cuando
#: el roto es el entorno de test.
#:
#: Con `YAPPY_WEB_DIST` apuntando a un directorio **vacío**, la ruta de la SPA no
#: se registra y el catch-all `/{full_path:path}` no se come nada. Es lo que
#: `backend/tests/conftest.py` setea para toda la suite: los tests que prueban el
#: camino de "no hay build" setean `FRONTEND_DIST` a un directorio inexistente.
FRONTEND_DIST = Path(os.environ["YAPPY_WEB_DIST"]) if os.environ.get("YAPPY_WEB_DIST") else project_root() / "frontend" / "dist" / "browser"


def _serve_spa(app: FastAPI) -> None:
    """Serve the compiled Angular app. Registered last so /api and docs win."""
    index = FRONTEND_DIST / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str = ""):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # `full_path` viene de una URL, o sea que puede traer `..`. Sin esta
        # comparación contra la raíz del build, el catch-all sirve cualquier
        # archivo del disco —`../../config/env.base`, que tiene el profile de
        # AWS, o `.env.local`, que tiene el token de RDS—. El server escucha en
        # `127.0.0.1`, así que alcanza con que un proceso local, o una página
        # web que el dev visite, haga un GET.
        #
        # `resolve()` antes de comparar porque `..` no se normaliza solo: sin
        # esto, `/%2e%2e/secret.txt` sale de la raíz y el `is_file()` lo
        # encuentra igual.
        candidate = (FRONTEND_DIST / full_path).resolve()
        if (
            full_path
            and candidate.is_relative_to(FRONTEND_DIST.resolve())
            and candidate.is_file()
        ):
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
    # Esta app no registra hooks de startup ni de shutdown, y eso es una
    # restricción, no una casualidad: `run.py` levanta uvicorn con
    # `lifespan="off"` para que el Ctrl+C no cierre cada sesión con veinte líneas
    # de stack trace. Agregar un hook acá lo haría correr en silencio — nunca.
    # Ver `tests/test_web_shutdown.py`, que falla si aparece uno.
    app = FastAPI(
        title="Yappy Region Sync",
        description="Diff de DB (tablas/SP) y de parámetros SSM/Secrets entre regiones de origen y destino",
        version="2.0.0",
    )
    app.include_router(envs.router)
    app.include_router(db.router)
    app.include_router(params.router)
    app.include_router(sessions.router)
    app.include_router(deployments.router)
    app.include_router(workflow.router)
    if FRONTEND_DIST.is_dir():
        _serve_spa(app)
    else:
        _serve_unavailable(app)
    return app


app = _create_app()
