"""La hora en los logs.

Dos fuentes distintas y una sola regla: **la hora va adelante**, porque al principio
se lee como columna y no hay que buscarla dentro de la línea. Y el formato es el
mismo en las dos —`%H:%M:%S`— porque si divergen las dos columnas de tiempo no
alinean y el log queda peor que sin hora.

Lo que se comprueba acá:

- el launch de uvicorn recibe un `log_config` con `asctime` en los dos formatters;
- el `log_config` **parte del de uvicorn** y no lo pisa entero, para no perder los
  `DefaultFormatter`/`AccessFormatter` que colorean el nivel;
- el prefijo no rompe ninguna de las mensajes que los tests del CLI ya verifican.
"""

import io

from yappy_api import run as run_mod
from yappy_library.adapters import logging as log_mod


def capture(call):
    """Run ``call`` with both rich consoles pointed at a buffer, and return the text.

    Ni `capsys` ni `capfd` alcanzan a `_err_console`: el `Console` de rich guardó la
    referencia al `sys.stderr` que existía al importarse el módulo, y pytest no
    reemplaza ese objeto, sólo el `sys.stderr` del módulo y el file descriptor.
    Por eso se le asigna un buffer al `Console`, que sí tiene setter para eso.

    Se guarda y restaura `_file`, el atributo interno, y **no** la propiedad
    `file`. La propiedad devuelve `sys.stdout`/`sys.stderr` cuando no se fijó nada;
    leerla y devolver ese valor después clava el `Console` al stdout que había en
    ese momento, y los tests siguientes escriben ahí en vez de a su propio capture.
    """
    buffer = io.StringIO()
    err_console, out_console = log_mod._err_console, log_mod._console
    before = (err_console._file, out_console._file)
    err_console._file = buffer
    out_console._file = buffer
    try:
        call()
    finally:
        err_console._file, out_console._file = before
    return buffer.getvalue()


def test_uvicorn_formatters_carry_the_timestamp():
    config = run_mod.log_config()

    for name in ("default", "access"):
        assert "%(asctime)s" in config["formatters"][name]["fmt"]
        assert config["formatters"][name]["datefmt"] == "%H:%M:%S"


def test_the_hour_comes_first_not_last():
    # Al principio se lee como columna. Al final hay que buscarla, y en el access
    # log el final lo ocupa el status code.
    config = run_mod.log_config()

    assert config["formatters"]["access"]["fmt"].startswith("%(asctime)s ")
    assert config["formatters"]["default"]["fmt"].startswith("%(asctime)s ")


def test_the_default_fmt_is_kept_after_the_timestamp():
    # Se agrega `asctime` al fmt de uvicorn en vez de reemplazarlo: perder el
    # `levelprefix` saca el "INFO:" del principio de cada línea.
    import uvicorn

    config = run_mod.log_config()
    original = uvicorn.config.LOGGING_CONFIG["formatters"]["access"]["fmt"]

    assert original in config["formatters"]["access"]["fmt"]
    assert "%(client_addr)s" in config["formatters"]["access"]["fmt"]
    assert "%(request_line)s" in config["formatters"]["access"]["fmt"]


def test_the_config_keeps_uvicorns_own_formatters_and_handlers():
    """Copiar el dict entero perdería el color del nivel, que es lo único que
    separa un warning de un error de un 200 a ojo."""
    import uvicorn

    config = run_mod.log_config()

    for name, formatter in uvicorn.config.LOGGING_CONFIG["formatters"].items():
        assert config["formatters"][name]["()"] == formatter["()"]
    assert set(config["handlers"]) == set(uvicorn.config.LOGGING_CONFIG["handlers"])
    assert config["loggers"]["uvicorn.access"]["handlers"] == ["access"]


def test_the_default_config_is_not_mutated():
    """`deepcopy` y no una referencia: si se mutara el dict de uvicorn, el segundo
    `run()` en el mismo proceso prependería la hora dos veces."""
    import uvicorn

    before = uvicorn.config.LOGGING_CONFIG["formatters"]["access"]["fmt"]
    run_mod.log_config()

    assert uvicorn.config.LOGGING_CONFIG["formatters"]["access"]["fmt"] == before


def test_both_sources_share_the_same_time_format():
    # Si divergieran, el `> Config dir` y el `INFO:` de la línea siguiente
    # mostrarían horas de formato distinto y no se alinearían.
    assert log_mod.LOG_TIME_FORMAT == "%H:%M:%S"


def test_every_message_carries_the_hour():
    import re

    combined = capture(
        lambda: [
            print_("mensaje")
            for print_ in (log_mod.info, log_mod.success, log_mod.warn, log_mod.command)
        ]
        + [log_mod.error("mensaje")]
    )

    # Cinco mensajes, cinco horas. Se verifica la forma y no el valor: la hora
    # cambia entre dos llamadas y un assert sobre un string fijo sería una
    # lotería que pasa una vez por minuto.
    stamps = re.findall(r"\d{2}:\d{2}:\d{2}", combined)
    assert len(stamps) == 5, combined
    # Y antes del mensaje: prefijo, no sufijo.
    assert combined.index(stamps[0]) < combined.index("mensaje")


def test_the_hour_precedes_the_marker():
    # El marcador (`>`, `OK`, `!!`, `ERR`, `$`) después de la hora: si la hora
    # fuera un sufijo, el grep por `^>` de los scripts dejaría de encontrar las
    # líneas que empiezan con marca.
    combined = capture(
        lambda: [
            log_mod.info("x"),
            log_mod.success("x"),
            log_mod.warn("x"),
            log_mod.command("aws ssm start-session"),
            log_mod.error("x"),
        ]
    )

    lines = [line for line in combined.splitlines() if line.strip()]
    assert len(lines) == 5, combined
    for line in lines:
        assert not line.startswith((">", "OK", "!!", "ERR", "$")), line
        assert ":" in line.split(" ")[0], line


def test_die_still_exits_after_logging():
    printed = capture(lambda: _expect_system_exit(log_mod.die, "fallo"))

    assert "fallo" in printed


def _expect_system_exit(func, *args):
    """`die` corta el proceso: se corre dentro de `capture` para no perder el texto."""
    import pytest

    with pytest.raises(SystemExit):
        func(*args)


def test_raw_stays_untimestamped():
    """`raw` imprime contenido, no un evento: es la salida de un comando, o de un
    `SHOW CREATE`, y un prefijo ahí se cuela dentro de lo que se copia."""
    printed = capture(lambda: log_mod.raw("CREATE TABLE `x` (`id` int);"))

    assert printed.strip() == "CREATE TABLE `x` (`id` int);"
