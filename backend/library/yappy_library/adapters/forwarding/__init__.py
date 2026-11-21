"""Los dos forwarders de un túnel, y cuál de los dos levanta una llamada.

``ssm`` es el de producción y el que corre sin que nadie haga nada: es
``aws ssm start-session``, detrás del puerto
:class:`~yappy_library.ports.port_forwarder.PortForwarder`. ``local`` es el mismo
mecanismo con el salto por AWS saco, para poder ejercitar tracker, probe,
supervisor, guard de puertos, cleanup y reconciliación contra los contenedores de
MySQL que ya corren en ``localhost``.

**El default es ``ssm`` y no se va a cambiar.** Cambiarlo sería cambiar el
comportamiento de todas las máquinas que no tocaron la config, y la forma de
descubrir que un default cambió es un túnel que no sale en la pantalla de
alguien que no tiene ni idea de esto. Opt-in explícito.
"""

from __future__ import annotations

from typing import cast

from yappy_library.config import Config
from yappy_library.ports.port_forwarder import PortForwarder

#: Lo que corre si nadie dice otra cosa.
DEFAULT_FORWARDER = "ssm"


def _configured() -> str:
    """``TUNNEL_FORWARDER`` leído por el accessor de la app.

    :meth:`Config.get` primero mira los archivos de config y después
    ``YAPPY_TUNNEL_FORWARDER``, así que la clave se puede dejar en el
    ``env.base`` de un ambiente y pisar por variable en una sesión suelta, sin
    que ningún call site tenga que saber de dónde salió.
    """
    return (
        Config().get("TUNNEL_FORWARDER", DEFAULT_FORWARDER) or DEFAULT_FORWARDER
    ).strip().lower()


def forwarder_for(name: str | None = None) -> PortForwarder:
    """El forwarder pedido, o el que dice la config.

    **Un nombre que no existe cae en ``ssm`` en vez de tirar**, por el mismo
    motivo que :func:`readiness.probe_for` cae en HTTP: un typo tiene que verse
    como algo que no funcionó —el error de la AWS CLI, o el log del túnel— y no
    como un 500 en la fila que se acaba de clickear. Lo que no puede pasar es
    que un ``TUNNEL_FORWARDER=loacl`` termine levantando un forwarder local en
    silencio.

    **El import de los dos módulos es adentro de la función.** ``local_forwarder``
    es ejecutable —``python -m yappy_library.adapters.forwarding.local_forwarder``
    lo corre como proceso— y ``runpy`` primero importa el paquete padre. Si acá
    arriba importáramos el módulo, quedaría en ``sys.modules`` antes de que
    ``runpy`` lo ejecute, que son dos copias del mismo código en memoria y un
    ``RuntimeWarning`` en stderr: o sea, una línea de ruido en
    ``~/.yappy/logs/tunnel-<port>.log`` en cada túnel local, que es
    justamente el archivo que el supervisor lee para explicar un fallo.

    Sin caché a propósito. Un ``lru_cache`` acá haría que cambiar
    ``YAPPY_TUNNEL_FORWARDER`` en la terminal no cambie nada en el web server
    que ya está corriendo, que es la clase de sorpresa que hace que alguien
    reinicie el server tres veces antes de mirar la config. Leer dos o tres
    archivos chicos por ``ssm_tunnel`` es nada al lado del proceso que esa misma
    función acaba de spawnear.
    """
    from . import local_forwarder, ssm_forwarder

    forwarders: dict[str, object] = {"ssm": ssm_forwarder, "local": local_forwarder}
    chosen = name.strip().lower() if name else _configured()
    return cast(PortForwarder, forwarders.get(chosen, forwarders[DEFAULT_FORWARDER]))
