"""Escribir un archivo sin dejar nunca uno a medias, y serializarlo.

Dos problemas que se parecen y que conviene resolver en un solo lugar.

**Un archivo a medias.** `path.write_text(...)` trunca y reescribe: entre las
dos operaciones el archivo está incompleto. Para el tracker eso era visible —
el parser del JSON truncado devuelve ``None`` y la fila **desaparece** del
snapshot, así que la UI parpadea—. Para ``~/.aws/credentials`` era peor: un
corte a mitad dejaba al developer sin claves de AWS.

Escribir a un temporal en la misma carpeta y renombrar encima lo arregla: el
rename es atómico en el mismo volumen, así que un lector ve el archivo viejo
entero o el nuevo entero, y nunca un tercero a medio escribir.

**Dos escritores a la vez.** El tracker lo escriben el CLI y la web, que son
procesos distintos. El catálogo de puertos lo edita la pantalla "en cada
tecla". Un read-modify-write sin lock pierde una de las dos ediciones, y
aunque no se perdiera, el rename final es el que decide: sin lock, el
último en escribir gana y se lleva el cambio del otro.

El lock es entre procesos a propósito: un ``threading.Lock`` no alcanza
cuando los que escriben son dos procesos distintos.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# `sys.platform` y no `os.name`: mypy sólo chequea la rama que corresponde
# a la plataforma en la que corre, así que el módulo de la otra plataforma
# no le da errors.
if sys.platform == "win32":  # pragma: no cover - la rama se ejercita en Windows
    import msvcrt

    def _lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - la rama se ejercita en Linux
    import fcntl

    def _lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replace(tmp: Path, path: Path, intentos: int = 30) -> None:
    """Renombrar el temporal encima del destino, reintentando si hace falta.

    **En Windows el rename necesita permiso de borrado sobre el destino**, y
    los readers de Python no lo comparten: un lector que tiene el archivo
    abierto lo bloquea, y el rename falla con ``PermissionError``. Eso no es
    una rareza —el tracker lo lee la web cada medio segundo—, así que el
    rename se reintenta: el lector suelta el handle en microsegundos.

    Sin el reintento, la escritura atómica sería *peor* que el ``write_text``
    que reemplaza: el truncate+write de antes podía competir con un lector,
    pero no fallar.
    """
    for intento in range(intentos):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if intento == intentos - 1:
                raise
            time.sleep(0.01)


def write_atomic(path: Path, payload: str, encoding: str = "utf-8") -> None:
    """Escribir un archivo entero, o no cambiarlo.

    El temporal va en la **misma carpeta** que el destino: el rename es
    atómico sólo en el mismo volumen, y ``os.replace`` sobre otro disco falla.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(payload, encoding=encoding)
        _replace(tmp, path)
    except BaseException:
        # Si algo falla, el temporal no queda tirado en la carpeta.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def lock_path_for(path: Path) -> Path:
    """El archivo de lock que acompaña a ``path``.

    Con punto adelante a propósito: ``env.base`` vive en un directorio que se
    lista con el glob ``env.*``, y un ``env.base.lock`` aparecería como un
    ambiente llamado ``base.lock``. ``.env.base.lock`` no matchea ese glob.
    """
    return path.with_name(f".{path.name}.lock")


@contextmanager
def file_lock(path: Path, timeout: float = 10.0) -> Iterator[None]:
    """Un lock exclusivo sobre ``path``, entre procesos.

    El lock vive en un archivo aparte —el del target—, así que no se
    interrumpe la lectura del contenido. Espera hasta ``timeout`` y después
    levanta: un lock que nunca se libera es peor que ninguno, porque cuelga
    al que lo espera.
    """
    lock_path = lock_path_for(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        # `msvcrt.locking` y `flock` painless sobre un byte: el archivo tiene
        # que existir con contenido, o el rango a bloquear no existe.
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"No se pudo tomar el lock de {path} en {timeout:g}s"
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)
    finally:
        handle.close()
