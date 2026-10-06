from __future__ import annotations

import sys
from datetime import datetime

from rich.console import Console
from rich.theme import Theme

_theme = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "dim": "bright_black",
})

_console = Console(theme=_theme)
_err_console = Console(theme=_theme, file=sys.stderr)
console = _console

#: Hora local, sin fecha ni milisegundos.
#:
#: Es lo que hace falta para leer un tail: la fecha es ruido mientras el proceso
#: corre, y los milisegundos sólo clutteran las líneas que llegan en ráfaga.
#: Queda como constante y no en las llamadas porque el log de uvicorn tiene que
#: usar el mismo formato — si divergen, las dos columnas de tiempo no alinean.
LOG_TIME_FORMAT = "%H:%M:%S"


def _stamp() -> str:
    return datetime.now().strftime(LOG_TIME_FORMAT)


def info(msg: str):
    _console.print(f"[dim]{_stamp()}[/dim] [info]>[/info] {msg}")


def success(msg: str):
    _console.print(f"[dim]{_stamp()}[/dim] [success]OK[/success] {msg}")


def warn(msg: str):
    _console.print(f"[dim]{_stamp()}[/dim] [warning]!![/warning] {msg}")


def error(msg: str):
    _err_console.print(f"[dim]{_stamp()}[/dim] [error]ERR[/error] {msg}")


def die(msg: str, code: int = 1):
    error(msg)
    sys.exit(code)


def raw(msg: str):
    _console.print(msg)


def command(cmd: str):
    _console.print(f"[dim]{_stamp()}[/dim] $ {cmd}")
