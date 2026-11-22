"""El contrato de "levantar un túnel", y lo que hay que saber para cumplirlo.

**El contrato no dice AWS.** Dice: *un proceso de host que haga bind de
``local_port`` y reenvíe TCP a ``remote_host:remote_port``*. ``aws ssm
start-session`` cumple ese contrato porque corre ese proceso **localmente** —
un ``session-manager-plugin`` con un pid de Windows — y usa AWS solamente para
alcanzar una red a la que el desarrollador no tiene ruta. Todo lo que el resto
del código depende del túnel (el tracker con su pid y su exe, el probe que
espera un saludo de MySQL y no un ``connect()``, el veredicto del supervisor, el
guard de ``taskkill /t``) está describiendo ese proceso local, no el transporte.

Por eso el puerto es ``argv``, y no "una sesión de SSM": lo que el llamador
sabe hacer es *pedir un proceso*, y lo que necesita es que ese proceso sea
fiel. :class:`PortForwarder` tiene un solo miembro justamente para que no haya
dónde meter un requerimiento que no sea el argv: si el contrato admitiese más
cosas, un forwarder nuevo podría cumplir la mitad y seguir siendo "el
forwarder", y eso se vería en un ``if`` escondido en vez de en el comando que
salió.

Los dos miembros implementados viven en
:mod:`yappy_library.adapters.forwarding`: :mod:`.ssm_forwarder` (el de
producción) y :mod:`.local_forwarder` (el mismo mecanismo con el salto por AWS
saco, para poder ejercitar todo el camino contra los contenedores de MySQL que
ya corren en ``localhost:3306/3307/3308``).

Estructural a propósito, como el ``SelectPlan`` de
:mod:`yappy_library.application.database.sync.migrate`: el contrato es "tené
estos miembros", no "heredá de esta clase", así que un forwarder nuevo no puede
driftar overriding uno de los dos y creer que cumple.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ForwardRequest:
    """Todo lo que el llamador sabe de un túnel, y nada más.

    Los campos son los que :meth:`yappy_library.adapters.processes.
    BaseCommand.ssm_tunnel` ya tenía en la mano cuando arma el comando; no hay
    ninguno derivado acá, para que el request no pueda empezar a decidir por su
    cuenta algo que el llamador no pidió.

    ``instance``/``region``/``profile`` los usa **sólo** ``ssm``; ``local`` los
    ignora porque su destino no pasa por AWS. Pero el destino **es el mismo en
    los dos**: ``local`` hace bind de ``local_port`` y reenvía a
    ``TUNNEL_LOCAL_HOST:remote_port`` —la región en loopback, con el mismo
    puerto remoto que reenviaría AWS—. Sólo cambia el host, porque el host
    remoto es un nombre que este proceso no resuelve. Ese espejo es lo que hace
    que la fila ``:remote_port → :local_port`` de la pantalla describa el
    proceso que corre, y no una intención.
    """

    instance: str
    region: str
    profile: str
    local_port: int
    remote_port: int
    remote_host: str | None = None
    env: str = ""


@runtime_checkable
class PortForwarder(Protocol):
    """El contrato entero entre "levantar un túnel" y "qué proceso lo levanta".

    Un solo miembro, y es ``argv`` a propósito. La parte difícil de este
    repositorio —el sink del log, el ``"w"`` por intento, el ``quiet`` a
    ``DEVNULL``, el ``track_process`` con sus once campos— vive en
    :meth:`BaseCommand.ssm_tunnel` y no se toca: si el forwarder devolviera el
    ``Popen`` en vez del comando, cada implementación tendría que volver a
    implementar el log y el tracking, y la primera que se equivocara al
    hacerlo escribiría un log que nadie encuentra.

    Lo que se delega es exactamente lo que AWS y un forwarder local no
    comparten: el binario y sus argumentos. Y como lo que sale es una lista de
    strings, el argv de ``ssm`` se puede pinear en un test como literal —que es
    lo único que separa "movimos código" de "rompimos los túneles de todos"—
    y el argv de ``local`` es legible en el log del túnel sin tener que
    reproducir nada.
    """

    def argv(self, request: ForwardRequest) -> list[str]:
        """El comando que levanta este túnel, listo para ``Popen``.

        Sin ``shell``: siempre lista y nunca string, como el resto de este
        módulo. Y sin efectos: no abre el log ni trackea nada, para que el
        orden en que :meth:`ssm_tunnel` abre el sink antes de spawnear y
        trackea después siga siendo el orden en que pasaban las cosas.
        """
        ...
