"""El forwarder de producción: ``aws ssm start-session``.

Sale de :meth:`yappy_library.adapters.processes.BaseCommand.ssm_tunnel` sin
cambiar un byte del argv que ese método armaba, y ese es el punto entero de este
archivo. El comando es la *interfaz* con AWS: el orden de los flags, el nombre
del documento, y ese ``json.dumps(params)`` que es un string opaco para el que
lo lee, son lo único que separa "movimos código" de "rompimos los túneles de
todos los que no pueden probar nada antes de levantar el web".

Por eso acá no hay un ``if forwarder == "ssm"`` ni una rama: hay un módulo con
un :func:`argv` y los flags en el mismo orden en que estaban, escritos en el
mismo orden que en :func:`BaseCommand.ssm_tunnel` los armaba.

**El import de ``_aws_cmd`` es adentro de la función, y no arriba del todo.**
Dos razones, y las dos importan. La primera es que ``processes`` importa este
paquete para elegir forwarder: traerlo acá arriba sería un ciclo. La segunda es
que lo que los tests tocan es el **atributo del módulo** (``processes._aws_cmd``),
no un nombre que este archivo copió al importar. Un ``from ... import _aws_cmd``
arriba del archivo quedaría desactualizado en el instante en que alguien lo
monkeypatchea, y un test que sigue verde probaría que el mock se aplicó a otro
lugar.
"""

from __future__ import annotations

import json

from yappy_library.ports.port_forwarder import ForwardRequest

#: Documento de SSM para "alcanzá este puerto de esta máquina". Sin
#: ``--parameters host``, el tráfico sale de la instancia misma.
DOC_LOCAL = "AWS-StartPortForwardingSession"

#: El otro documento, para cuando la sesión tiene que llegar a un host que *no*
#: es la instancia — un pod, un nodo, un service de otro cluster. Es el caso que
#: además está atado a Windows: ``ssm_tunnel`` se niega a arrancar este documento
#: en Linux, porque el ``sys.exit(1)`` que le ponía atrás cuando se levantaba un
#: remote host ahí se llevaba uvicorn sin dejar traceback.
DOC_REMOTE_HOST = "AWS-StartPortForwardingSessionToRemoteHost"


def document_name(remote_host: str | None) -> str:
    """Qué documento va, según haya host remoto o no.

    La condición es la misma que usaba el código movido —truthiness del
    ``remote_host``— y no ``is not None``: un ``""`` explícito significaba, y
    sigue significando, "sin host".
    """
    return DOC_REMOTE_HOST if remote_host else DOC_LOCAL


def parameters(request: ForwardRequest) -> dict:
    """Los parámetros del documento, en el orden que los declara el SSM.

    ``json.dumps`` respeta el orden de inserción, así que el orden de las claves
    es parte del comando: cambiarlo produce el mismo JSON con otro texto y un
    tunnel log distinto del que se conoce.
    """
    if request.remote_host:
        return {
            "portNumber": [str(request.remote_port)],
            "host": [request.remote_host],
            "localPortNumber": [str(request.local_port)],
        }
    return {
        "portNumber": [str(request.remote_port)],
        "localPortNumber": [str(request.local_port)],
    }


def argv(request: ForwardRequest) -> list[str]:
    """El comando exacto que hoy levanta un túnel SSM.

    El orden de los flags es el de siempre y no se toca:
    ``start-session --target --document-name --parameters --profile --region``.
    Si alguien lo reordena, AWS sigue aceptando el comando — es por eso que el
    cambio pasaría inadvertido— y el argv pineado en
    ``tests/test_tunnel_forwarder.py`` es lo que lo hace fallar en vez de
    dejarlo pasar.
    """
    from yappy_library.adapters import processes

    return [
        *processes._aws_cmd(), "ssm", "start-session",
        "--target", request.instance,
        "--document-name", document_name(request.remote_host),
        "--parameters", json.dumps(parameters(request)),
        "--profile", request.profile,
        "--region", request.region,
    ]
