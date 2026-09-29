"""FastAPI app factory — yappy-web-api.

Bind exclusivo a 127.0.0.1 (ver comando `yappy web api` en cli/verbs/run.py).
No hay autenticación: este servicio está pensado para correr solo en localhost
como herramienta de desarrollo interna. No expandir el bind a 0.0.0.0 sin
agregar autenticación primero.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .error_handlers import register_error_handlers
from .ports_registry import YappyPort
from .routers import environments, parameters


def create_app() -> FastAPI:
    app = FastAPI(
        title="Yappy Web API",
        description="Backend interno del devtool web de Yappy (parámetros, DB explorer, queries).",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{YappyPort.WEB_UI_DEV}"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(environments.router)
    app.include_router(parameters.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
