"""uvicorn launcher for the Region Sync web UI."""

from __future__ import annotations

import os
import threading
import webbrowser
from copy import deepcopy

from yappy_library.adapters.logging import LOG_TIME_FORMAT, error, info
from yappy_library.config import Config

from . import shutdown

#: How long the shutdown waits for a connection that did not close on its own.
#:
#: Los streams SSE se cierran solos apenas la señal llega (ver ``_Server`` y
#: :mod:`yappy_api.shutdown`), así que un apagado normal no llega acá: esto es
#: el respaldo para lo que sí se puede clavar de verdad — un request de DB que
#: no vuelve — donde esperar para siempre es peor que cancelar. Sin él uvicorn
#: espera a que **todas** las conexiones se cierren, y "todas" incluía una que
#: nunca se cierra.
GRACEFUL_SHUTDOWN_SECONDS = 5


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

    # El tracker es un registro en disco que sobrevive al proceso que nombra, así
    # que al arrancar hay entradas de sesiones web anteriores: procesos muertos,
    # pids reciclados y huérfanos de una sesión que ya no existe. `reconcile()`
    # decide qué hacer con cada una una vez y lo deja asentado en el log. Va
    # antes de cualquier otra cosa porque todo lo que sigue lee ese registro.
    #
    # El `try/except` es la parte importante: una limpieza rota no puede costarle
    # la UI al que la llamó. Peor todavía, con `lifespan="off"` no hay un hook de
    # startup donde atar esto (ver más abajo y `tests/test_web_shutdown.py`), así
    # que este es el único lugar del que sale.
    from .reconcile import reconcile

    try:
        reconcile()
    except Exception as exc:  # noqa: BLE001 - el server tiene que levantar igual
        error(f"La reconciliación del tracker falló ({exc}). La web levanta igual.")

    # Los clientes de AWS se calientan en un hilo aparte para que el server
    # levante ya: el trabajo es el mismo en los dos caminos, pero bloquear el
    # arranque agregaría su duración a cada `yappy web`.
    from .warmup import warm

    warm()

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    _log_shutdown_signal()
    _serve(host=host, port=port)


def _serve(host: str, port: int) -> None:
    """Levantar uvicorn con el Ctrl+C cableado a los streams SSE.

    Hecho a mano en vez de un ``uvicorn.run(app, ...)`` por una sola razón:
    ``uvicorn.run`` crea el ``Server`` adentro suyo y no deja forma de tocarlo,
    y el ``handle_exit`` de ese server es el único lugar donde este proceso se
    entera de la señal — uvicorn instala su propio handler mientras sirve y no
    lo encadena con el que ya había (``capture_signals``). Sin ese gancho, la
    terminal moría pegada en ``Waiting for connections to close. (CTRL+C to
    force quit)``: ver ``_Server`` abajo y :mod:`yappy_api.shutdown`.
    """
    import uvicorn

    from .app import app

    class _Server(uvicorn.Server):
        """Uvicorn, con la señal de salida conectada al flag de apagado.

        ``handle_exit`` es lo primero que corre con el Ctrl+C, y corre antes de
        que uvicorn empiece a cerrar conexiones. Ahí se levanta el flag que los
        dos streams SSE miran en cada vuelta: los generadores se cierran solos,
        las conexiones se van, y el shutdown graceful encuentra el camino despejado
        en vez de esperar a una conexión SSE — que no se cierra sola, espera un
        cambio que puede no venir nunca.
        """

        def handle_exit(self, sig, frame):
            shutdown.begin()
            super().handle_exit(sig, frame)

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
    # Un `except KeyboardInterrupt` alrededor del arranque no lo arregla: eso
    # tapa ruido de verdad también. Esta app no usa el protocolo: no hay startup
    # hooks, ni shutdown hooks, ni context manager de lifespan. El calentamiento
    # de clientes se dispara en `run()`, antes de que uvicorn levante nada. Hay un
    # test que falla si alguien agrega uno — con `lifespan="off"` se ignoraría en
    # silencio.
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=log_config(),
        lifespan="off",
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    try:
        _Server(config).run()
    except KeyboardInterrupt:
        # Uvicorn traga esto adentro de `uvicorn.run`; al correr el Server a mano
        # hay que tragácelo acá. No llega del Ctrl+C — ese lo consume
        # `handle_exit` y termina en un exit limpio — sino del SIGINT que uvicorn
        # **relanza** al terminar el shutdown, para que el handler que había antes
        # del suyo haga lo suyo (`capture_signals` lo re-levanta después de
        # restaurar los handlers; ver `_log_shutdown_signal`). Un error real de
        # arranque, como un puerto ocupado, no es un KeyboardInterrupt y sigue
        # subiendo — lo testea `test_web_shutdown.py`.
        pass


def _log_shutdown_signal():
    """Say which signal is shutting the server down, before uvicorn eats it.

    Uvicorn installs its own handlers, so by the time anything visible happens
    the signal has already been swallowed. That leaves the worst question
    unanswered: *why did the server stop?* — Ctrl+C from a human, a `taskkill`
    from another process, or a terminal that closed.

    The handler logs and then chains to whatever was there before, so uvicorn
    still does its graceful shutdown. It only claims SIGINT and SIGTERM; SIGHUP
    and the rest are left alone.
    """
    import signal

    from yappy_library.adapters.logging import info

    previous: dict[int, object] = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        if sig in previous:
            continue
        try:
            previous[sig] = signal.getsignal(sig)

            def handler(signum, frame, _prev=previous):
                try:
                    info(f"  [apagado] señal {signal.Signals(signum).name} recibida")
                except Exception:  # noqa: BLE001 - loggear no puede romper el apagado
                    pass
                handler._prev = getattr(handler, "_prev", None)
                old = handler._prev.get(signum)
                if callable(old):
                    return old(signum, frame)
                return None

            handler._prev = previous
            signal.signal(sig, handler)
        except (OSError, ValueError):
            # Sin consola propia (o signal no disponible): no se puede escuchar.
            pass


if __name__ == "__main__":
    run()
