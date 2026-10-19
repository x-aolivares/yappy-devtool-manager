"""El apagado de la web tiene que ser silencioso en Windows.

Un Ctrl+C en Windows cerraba cada `yappy web` con veinte líneas de stack trace.
No era un crash — el servidor se apagaba igual y el comando terminaba bien — pero
era ruido justo donde uno mira cuando algo salió mal.

**Lo que NO era.** Durante dos commits se intentó atrapar la excepción con un
`except KeyboardInterrupt` alrededor de `uvicorn.run`. No podía funcionar:
`uvicorn.run` ya tiene su propio `except KeyboardInterrupt` (`uvicorn/main.py`) y
se traga la excepción antes de que llegue a nadie. Los tests de ese intento
monkeypatcheaban `uvicorn.run` para que levantara algo que en la realidad nunca
levanta, así que pasaban probando una ficción.

**Lo que sí era.** El protocolo lifespan de uvicorn levanta una tarea esperando en
`receive()`. Con un request en vuelo, el Ctrl+C entra en shutdown graceful y la
cancela; su `await receive()` levanta `CancelledError`, que es `BaseException`, y
uvicorn lo registra como "el protocolo lifespan falló" con `exc_info`
(`uvicorn/lifespan/on.py`). Ahí se imprime, adentro de uvicorn.

Por eso la solución es no levantar esa tarea: `lifespan="off"`. Y por eso esta app
puede hacerlo — no usa el protocolo para nada.

**La otra cara del apagado: la terminal colgada.** Un Ctrl+C con la web abierta
dejaba la terminal esperando en "Waiting for connections to close. (CTRL+C to
force quit)" — para siempre. La causa eran los streams SSE: su generator nunca
termina, y el shutdown graceful de uvicorn espera a que **todas** las
conexiones se cierren. La solución no es apurar a uvicorn: es que los streams
se cierren solos cuando llega la señal, cableados en el `handle_exit` del
Server — y por eso el launcher corre un `uvicorn.Server` propio en vez de un
`uvicorn.run`: éste crea el Server adentro suyo y no deja tocarlo. Estos
tests fijan las dos cosas que ese gancho no puede perder: `lifespan="off"` y
que un error real de arranque siga subiendo.
"""

from __future__ import annotations

import signal

import pytest


def test_el_servidor_se_levanta_sin_protocolo_lifespan(monkeypatch):
    """El contrato que arregla el traceback.

    Es lo único que se puede verificar de este bug: el evento de consola de
    Windows no se le manda a un proceso hijo desde el entorno de tests, así que la
    reproducción end-to-end no existe acá. Lo que sí es comprobable es que
    uvicorn levante sin la tarea del lifespan — que es la tarea que se cancela y
    la que loguea el error — y con un tope para el shutdown, que es lo que
    garantiza que la terminal nunca se quede esperando para siempre."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    captured = {}

    def fake_run(self):
        captured["lifespan"] = self.config.lifespan
        captured["timeout_graceful_shutdown"] = self.config.timeout_graceful_shutdown

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)

    web_run.run(open_browser=False)

    assert captured["lifespan"] == "off"
    assert captured["timeout_graceful_shutdown"] == web_run.GRACEFUL_SHUTDOWN_SECONDS


def test_la_app_no_registra_hooks_de_lifespan():
    """El guard que paga el riesgo de `lifespan="off"`.

    Con el protocolo apagado, un `@app.on_event("startup")` o un `@app.on_startup`
    se ignoraría **en silencio**: el server levanta, el hook no corre, y no hay
    ningún error que lo delate. Este test lo convierte en un build rojo.

    Cubre la API de hooks de FastAPI (`on_event`, `on_startup`, `on_shutdown`).
    No cubre un `lifespan=` propio pasado al constructor de `FastAPI`, que se
    detectaría comparando contra un default de Starlette — pero ese default es
    privado y cambia de forma entre versiones, y un assert frágil es peor que
    ninguno. Para ese caso está la nota en `app.py`.
    """
    from yappy_api.app import app

    assert app.router.on_startup == [], (
        "La app registró un hook de startup. Con lifespan='off' no se ejecutaría "
        "y el fallo sería silencioso."
    )
    assert app.router.on_shutdown == [], (
        "La app registró un hook de shutdown. Con lifespan='off' no se ejecutaría "
        "y el fallo sería silencioso."
    )


def test_calentar_sigue_pasando_por_antes_de_uvicorn(monkeypatch):
    """Los clientes se calientan antes de que uvicorn levante nada.

    Con `lifespan="off"` ya no hay un hook de startup donde colgarlo, así que el
    orden importa: si alguien lo moviera adentro, dejaría dessaothermarse."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    order: list[str] = []

    monkeypatch.setattr(uvicorn.Server, "run", lambda self: order.append("uvicorn"))
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: order.append("warm"))

    web_run.run(open_browser=False)

    assert order == ["warm", "uvicorn"]


def test_un_error_real_de_arranque_sigue_subiendo(monkeypatch):
    """Apagar sin ruido no puede significar tapar un error de verdad."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    def boom(self):
        raise RuntimeError("el puerto 8765 está ocupado")

    monkeypatch.setattr(uvicorn.Server, "run", boom)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="ocupado"):
        web_run.run(open_browser=False)


def test_el_ctrl_c_levanta_el_flag_que_cierra_los_streams(monkeypatch):
    """El gancho que evita la terminal colgada, en el momento exacto.

    `handle_exit` es lo primero que corre con el Ctrl+C — uvicorn lo instala como
    handler de señales mientras sirve — y corre **antes** de que uvicorn empiece a
    cerrar conexiones. Llamarla acá es lo mismo que un Ctrl+C, con la misma cadena
    que recorre uvicorn. Lo que se fija es el efecto: el flag que los dos streams
    SSE miran en cada vuelta, que los cierra y devuelve la terminal.
    """
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import shutdown
    from yappy_api import warmup

    def fake_run(self):
        # Dos veces, como el segundo Ctrl+C que uvicorn usa para forzar: tiene
        # que ser un no-op, no un segundo apagado.
        self.handle_exit(signal.SIGINT, None)
        self.handle_exit(signal.SIGINT, None)
        assert self.force_exit, "el segundo Ctrl+C no llegó a forzar la salida"

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)

    web_run.run(open_browser=False)

    assert shutdown.is_set(), "el Ctrl+c no levantó el flag que cierra los streams"