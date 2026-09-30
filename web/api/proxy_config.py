"""Proxy config del dev server de Angular.

Se genera desde `YappyPort` en vez de vivir como un JSON commiteado, para que el
puerto de la API tenga una sola fuente de verdad. Un `proxy.conf.json` a mano
termina desincronizado del enum en el primer cambio de puerto, y el síntoma es
un 404 confuso en vez de un error claro.

Lo consume `yappy web` vía `--proxy-config`. Un `ng serve` a pelo no lo usa: en
ese caso la UI llama a la API por origen cruzado y el CORS del backend, que
acepta `localhost` y `127.0.0.1`, resuelve.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Nombre del archivo generado. Gitignored.
GENERATED_NAME = ".proxy.generated.json"

_CONFIG = {
    "/api": {
        # pathRewrite saca el prefijo: el backend monta /environments, no
        # /api/environments. El prefijo existe solo para que el dev server sepa
        # qué reenviar.
        "pathRewrite": {"^/api": ""},
        "secure": False,
        "changeOrigin": False,
        # Sin logs: son consultas a una base de datos y el ruido no aporta.
        "logLevel": "warn",
    }
}


def path(frontend_dir: Path) -> Path:
    return frontend_dir / GENERATED_NAME


def write(frontend_dir: Path, api_port: int) -> Path:
    """Escribe el proxy conf apuntando al puerto de `YappyPort.WEB_API`."""
    from .ports_registry import YappyPort

    target_port = api_port or int(YappyPort.WEB_API)
    config = json.loads(json.dumps(_CONFIG))  # copia profunda
    config["/api"]["target"] = f"http://127.0.0.1:{target_port}"

    target = path(frontend_dir)
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return target
