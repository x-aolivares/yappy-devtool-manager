"""FastAPI app factory — yappy-web-api.

Bind exclusivo a 127.0.0.1 (ver comando `yappy web` en cli/verbs/web.py).
No hay autenticación: este servicio está pensado para correr solo en localhost
como herramienta de desarrollo interna. No expandir el bind a 0.0.0.0 sin
agregar autenticación primero.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .error_handlers import register_error_handlers
from .ports_registry import YappyPort
from .routers import databases, environments, local_mysql, migrate, parameters, query


def allowed_origins() -> list[str]:
    """Both spellings of loopback.

    The Angular dev server answers on `localhost` but `http://127.0.0.1:4300` is
    an equally valid thing to type in the address bar — and it is a *different*
    origin, so a list with only `localhost` gets silently blocked by CORS.
    """
    port = YappyPort.WEB_UI_DEV
    return [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Yappy Web API",
        description="Backend interno del devtool web de Yappy (parámetros, DB explorer, queries).",
        version="0.2.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(environments.router)
    app.include_router(parameters.router)
    app.include_router(databases.router)
    app.include_router(migrate.router)
    app.include_router(query.router)
    app.include_router(local_mysql.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
