"""uvicorn launcher for the Region Sync web UI."""

from __future__ import annotations

import os
import threading
import webbrowser
from copy import deepcopy

from yappy_library.adapters.logging import LOG_TIME_FORMAT, info
from yappy_library.config import Config


def log_config() -> dict:
    """Uvicorn's logging config with a timestamp on every line.

    Uvicorn's default formatters are ``"%(levelprefix)s %(message)s"``, sin hora:
    el access log delega la hora al que esté pipeando. Viendo la web en la
    terminal —que es como se usa— no hay ningún reloj, y sin él no se puede
    saber si un request tardó medio segundo o medio minuto, ni separar dos
    corridas pegadas.

    Se parte de :data:`uvicorn.config.LOGGING_CONFIG` y se le agregan
    ``%(asctime)s`` y ``datefmt`` en vez de escribir el dict entero, para no
    perder lo que uvicorn agregue en una versión nueva: los ``DefaultFormatter``
    y ``AccessFormatter`` son suyos y saben cómo colorean el nivel.

    La hora va **adelante**, no al final: al principio se lee como columna y no
    hay que buscarla dentro de la línea.
    """
    import uvicorn

    config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    for name in ("default", "access"):
        config["formatters"][name]["fmt"] = "%(asctime)s " + config["formatters"][name]["fmt"]
        config["formatters"][name]["datefmt"] = LOG_TIME_FORMAT
    return config


def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start the Region Sync web UI (blocking).

    El default es 8765, no 8000: el 8000 lo reservan vLLM y los servidores
    OpenAI-compatible, y el auto-discovery de OpenCode sondea ese puerto cada
    30s. Ver el comentario en :func:`yappy_cli.cli.web`.
    """
    import uvicorn

    from .app import app

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

    # Los clientes de AWS se calientan en un hilo aparte para que el server
    # levante ya: el trabajo es el mismo en los dos caminos, pero bloquear el
    # arranque agregaría su duración a cada `yappy web`.
    from .warmup import warm

    warm()

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # `lifespan="off"` y no un try/except alrededor del arranque.
    #
    # El protocolo lifespan de uvicorn levanta una tarea que se queda esperando
    # en `receive()`. Cuando el Ctrl+C cae con un request en vuelo, uvicorn entra
    # en shutdown graceful y **cancela esa tarea**; su `await receive()` levanta
    # `CancelledError`, que es un `BaseException`, y uvicorn lo toma como "el
    # protocolo lifespan falló" y lo loguea con `exc_info`
    # (`uvicorn/lifespan/on.py`). De ahí salían veinte líneas de stack trace al
    # final de cada `yappy web`.
    #
    # Un `except KeyboardInterrupt` alrededor de `uvicorn.run` no lo arregla:
    # `uvicorn.run` ya tiene el suyo (`uvicorn/main.py`) y traga la excepción
    # antes de que llegue acá. El traceback se loguea adentro, no se propaga.
    #
    # Esta app no usa el protocolo: no hay startup hooks, ni shutdown hooks, ni
    # context manager de lifespan. El calentamiento de clientes se dispara acá
    # mismo, antes de que uvicorn levante nada. Hay un test que falla si alguien
    # agrega uno — con `lifespan="off"` se ignoraría en silencio.
    uvicorn.run(app, host=host, port=port, log_config=log_config(), lifespan="off")


if __name__ == "__main__":
    run()
