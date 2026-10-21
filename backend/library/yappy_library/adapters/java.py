"""Los procesos Java de la máquina, y cómo pararlos.

``jps -l`` es la forma de preguntar qué JVMs hay: una línea por proceso, con
el pid y el nombre de la clase o del jar.

Había **cinco** parsers de ese output, y cada uno además decidía distinto a
qué procesos mata y cómo lo hace: uno mataba el broker y la UI a la fuerza,
otro paraba la UI pidiéndole que se vaya, otro mataba sólo el broker, y el
desinstalador mataba los dos. La lista de procesos Java es la misma para
todos; lo que cambia es **qué se hace con cada uno**, y eso es decisión de
quien llama, no del parser.

Por eso este módulo tiene dos cosas separadas: ``java_processes()`` dice qué
hay, y el que para decide si lo pide o lo mata. La asimetría está a la vista
y no escondida en dos líneas de un ``for``.
"""

from __future__ import annotations

import subprocess

from yappy_library.adapters.logging import raw
from yappy_library.adapters.processes import stop_gracefully

#: Cuánto espera el cierre elegante antes de matar a la fuerza.
#:
#: Treinta segundos, que es lo que usaba el broker: un JVM que no atiende el
#: evento de cierre en ese plazo no lo va a atender nunca.
GRACEFUL_STOP_SECONDS = 30.0


def java_processes() -> list[tuple[str, str]]:
    """Los procesos Java de la máquina, como pares ``(pid, nombre)``.

    Vacío si ``jps`` no está: no es un error —una máquina puede no tener
    Java—, pero quien llama tiene que saber que no encontró nada. Por eso la
    lista vacía y el error no son la misma cosa: acá no hay excepción.
    """
    try:
        result = subprocess.run(
            ["jps", "-l"], capture_output=True, text=True, check=False, timeout=10
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    procesos: list[tuple[str, str]] = []
    for line in result.stdout.strip().splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            procesos.append((parts[0], parts[1]))
    return procesos


def matching(pattern: str) -> list[tuple[str, str]]:
    """Los procesos Java cuyo nombre contiene ``pattern``."""
    return [(pid, name) for pid, name in java_processes() if pattern in name]


def kill_java(pid: str) -> None:
    """Matar un JVM a la fuerza.

    Es el último recurso, y tiene un costo: un broker que muere así deja el
    log de metadata a medias, y el próximo arranque lo encuentra inválido.
    Por eso :func:`stop_java` pregunta primero.
    """
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)


def stop_java(pid: str) -> bool:
    """Pedirle a un JVM que se vaya, y matarlo sólo si no se va.

    **Un broker que muere a la fuerza deja su metadata log a medias.** El
    arranque siguiente encuentra el offset inválido, borra el segmento y muere
    con un NPE — y desde ahí todo arranque falla igual, hasta que alguien
    limpia el storage. Preguntar primero es lo que mantiene el storage
    usable.

    El cierre limpio necesita una consola a la que mandarle el evento, que es
    por qué el broker se levanta con una (ver
    :func:`~yappy_library.adapters.processes.own_console_popen_kwargs`).

    Devuelve si el proceso se cerró solo.
    """
    try:
        pid_number = int(pid)
    except (TypeError, ValueError):
        return False
    if stop_gracefully(pid_number, timeout=GRACEFUL_STOP_SECONDS):
        raw(f"  - el proceso {pid} se cerró solo")
        return True
    kill_java(pid)
    return False
