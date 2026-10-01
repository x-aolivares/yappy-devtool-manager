"""uvicorn launcher for the Region Sync web UI."""

from __future__ import annotations

import os
import threading
import webbrowser

from ..config import Config
from ..logger import info


def run(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    """Start the Region Sync web UI (blocking)."""
    import uvicorn

    from .main import app

    config_dir = Config._get_config_dir()
    envs = Config.known_environments()
    if os.environ.get("YAPPY_CONFIG_DIR"):
        info(
            f"YAPPY_CONFIG_DIR override activo: "
            f"{os.environ['YAPPY_CONFIG_DIR']} -> {config_dir}"
        )
    if not config_dir.is_dir() or not envs:
        info(
            "ADVERTENCIA: no se encontraron ambientes. "
            f"Config dir resuelto: {config_dir}. "
            "Si las regiones están en otra carpeta, exportá YAPPY_CONFIG_DIR=<ruta>/config."
        )
    else:
        info(f"Config dir: {config_dir}")
        info(f"Ambientes: {', '.join(envs)}")

    url = f"http://{host}:{port}"
    info(f"Region Sync web -> {url}")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()