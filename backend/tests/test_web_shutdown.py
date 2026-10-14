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
"""

from __future__ import annotations

import pytest


def test_el_servidor_se_levanta_sin_protocolo_lifespan(monkeypatch):
    """El contrato que arregla el traceback.

    Es lo único que se puede verificar de este bug: el evento de consola de
    Windows no se le manda a un proceso hijo desde el entorno de tests, así que la
    reproducción end-to-end no existe acá. Lo que sí es comprobable es que
    uvicorn arranque sin la tarea del lifespan — que es la tarea que se cancela y
    la que loguea el error."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    captured = {}

    def fake_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)

    web_run.run(open_browser=False)

    assert captured["lifespan"] == "off"


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

    monkeypatch.setattr(uvicorn, "run", lambda app, **k: order.append("uvicorn"))
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: order.append("warm"))

    web_run.run(open_browser=False)

    assert order == ["warm", "uvicorn"]


def test_un_error_real_de_arranque_sigue_subiendo(monkeypatch):
    """Apagar sin ruido no puede significar tapar un error de verdad."""
    import uvicorn

    from yappy_api import run as web_run
    from yappy_api import warmup

    def boom(app, **kwargs):
        raise RuntimeError("el puerto 8765 está ocupado")

    monkeypatch.setattr(uvicorn, "run", boom)
    monkeypatch.setattr(warmup, "warm", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="ocupado"):
        web_run.run(open_browser=False)