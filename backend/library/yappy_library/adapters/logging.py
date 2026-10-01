import sys
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


def info(msg: str):
    _console.print(f"[info]>[/info] {msg}")


def success(msg: str):
    _console.print(f"[success]OK[/success] {msg}")


def warn(msg: str):
    _console.print(f"[warning]!![/warning] {msg}")


def error(msg: str):
    _err_console.print(f"[error]ERR[/error] {msg}")


def die(msg: str, code: int = 1):
    error(msg)
    sys.exit(code)


def raw(msg: str):
    _console.print(msg)


def command(cmd: str):
    _console.print(f"[dim]$ {cmd}[/dim]")
