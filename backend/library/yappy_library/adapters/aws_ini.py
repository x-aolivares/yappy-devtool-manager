"""Lectura y escritura de los archivos INI de AWS (`~/.aws/config`, `~/.aws/credentials`).

Viven en la library y no en el CLI por dos razones: el login SSO los necesita y
``Session.start()`` —que está en la library— los necesita también, y la library
no puede importar el CLI. Y porque un INI con dos parsers distintos es un INI
que se interpreta de dos maneras: el parser que borra una sección repetida y el
que la conserva no devuelven lo mismo para el mismo archivo.
"""

from __future__ import annotations

import os
from pathlib import Path


def aws_dir() -> Path:
    """La carpeta `~/.aws` del usuario."""
    return Path.home() / ".aws"


def read_ini(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Return (section_order, sections) from a simple INI file.

    Una sección repetida **no** se pisa: se conserva la primera y la segunda se
    fusiona. En `~/.aws/config` eso es lo correcto —`profile x` puede aparecer
    partido en dos bloques— y lo que hace el parser viejo, que reiniciaba el
    diccionario, era perder la mitad de la configuración.
    """
    order: list[str] = []
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    if path.exists():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                if current not in sections:
                    sections[current] = {}
                    order.append(current)
            elif (
                current is not None
                and "=" in stripped
                and not stripped.startswith(("#", ";"))
            ):
                k, v = stripped.split("=", 1)
                sections[current][k.strip()] = v.strip()
    return order, sections


def write_atomic(path: Path, payload: str) -> None:
    """Escribir un archivo de una sola vez, sin dejar nunca uno a medias.

    Se escribe a un temporal en la misma carpeta y se renombra encima: el
    rename es atómico en el mismo volumen, así que un corte a mitad —un disco
    lleno, un proceso muerto— deja el archivo viejo entero o el nuevo entero,
    y nunca un tercero a medio escribir. Escribiendo directo, un corte dejaba
    un `~/.aws/credentials` sin las claves.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)

    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def write_ini(path: Path, order: list[str], sections: dict[str, dict[str, str]]) -> None:
    """Escribir un INI, en el orden de las secciones que le pasó el caller.

    Se escribe entero y de una: ver `write_atomic` para por qué.
    """
    lines = []
    for name in order:
        lines.append(f"[{name}]")
        for k, v in sections[name].items():
            lines.append(f"{k} = {v}")
    write_atomic(path, "\n".join(lines) + "\n")
