"""Un forwarder local: el mismo túnel con el salto por AWS sacado.

``aws ssm start-session`` cumple el contrato de
:class:`~yappy_library.ports.port_forwarder.PortForwarder` porque corre **un
proceso local** —``session-manager-plugin``, con pid de Windows, con puerto
local— y usa AWS sólo para alcanzar una red a la que la máquina del
desarrollador no tiene ruta. Lo que queda después de sacarle AWS no es una
simulación de AWS: es el forwarder, tal cual.

Por eso este módulo sirve para dos cosas que antes nadie podía probar en una
máquina sin AWS:

- **``--startup-delay``**: el puerto local no escucha en el instante del spawn.
  Es lo que hace que ``connection._wait_for_port(port, timeout=30.0)`` exista, y
  lo que hace que la fila de la pantalla diga "levantando" en vez de "murió".
- **``--idle-timeout``**: una sesión de Session Manager se termina por
  inactividad, y por eso la fila pasa de ``up`` a ``dead`` sin error y sin que
  nadie haya hecho nada. Es el comportamiento que más cuesta reproducir sin
  AWS y el que más se ejercita acá.

Lo demás está para que los caminos que ya existen tengan contra qué fallar:
``--stall-after`` levanta un puerto que no contesta (el veredicto "abrió el
puerto pero no respondió" de ``supervisor.py:271-275``, contra un socket
genuinamente mudo y no contra un mock), ``--die-after`` se muere en medio de la
sesión (un proceso trackeado y muerto, que es lo que ``reconcile()`` tiene que
limpiar), y un puerto ya tomado sale por stderr con código 2 en vez de quedar
pareciendo que todavía está levantando — el bug que está escrito en
``processes.py:395-398``.

**Es un proceso de host con pid de verdad.** ``sys.executable -m ...`` a través
de :meth:`BaseCommand.popen`, nunca un thread: ``track_process``, ``kill_ssm`` y
``process_identity`` leen del sistema operativo, y un thread no tiene exe, ni
create_time, ni lugar en :mod:`process_tracker`.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

from yappy_library.config import Config
from yappy_library.ports.port_forwarder import ForwardRequest

#: Cómo se invoca a sí mismo. Va como ``-m`` y no como ruta al archivo porque el
#: child corre con el ``cwd`` de quien lo spawneó —el web server, el CLI— y una
#: ruta relativa a un archivo deja de existir en cuanto ese cwd cambia.
MODULE = "yappy_library.adapters.forwarding.local_forwarder"

# --- defaults del runner ------------------------------------------------------
# Los mismos valores que puede pisar ``TUNNEL_LOCAL_*``, en un solo lugar: el
# `argparse` y el ejemplo de config los toman de acá, así que no pueden divergir.

#: Loopback, como un ``local_port``: nada de esto sale de la máquina.
DEFAULT_REMOTE_HOST = "127.0.0.1"

#: 3306 es el MySQL de dev: el primer contenedor que levanta el stack y el único
#: que está de pie en una máquina limpia. El default tiene que ser algo con qué
#: hablar —un destino que no existe hace que todos los túneles fallen por la
#: misma razón, y "el forwarder está roto" queda indistinguible de "el forwarder
#: no tiene a quién pegarle"—.
DEFAULT_REMOTE_PORT = 3306

#: Segundos entre el spawn y el bind. Una sesión de SSM real tarda segundos en
#: establecerse: el probe tiene que aguantar "todavía no" sin declarar un fallo.
DEFAULT_STARTUP_DELAY = 3.0

#: Segundos sin mover un byte antes de terminar la sesión. Session Manager corta
#: las suyas por inactividad, y ese corte es la razón de que una fila pase de
#: ``up`` a ``dead`` sola.
DEFAULT_IDLE_TIMEOUT = 60.0

#: 0 = nunca, en los dos. El default tiene que ser "no romper nada": un
#: simulador que por defecto se para a los 5s sirve para una prueba y para
#: ninguna otra cosa.
DEFAULT_STALL_AFTER = 0.0
DEFAULT_DIE_AFTER = 0.0

#: Dos cosas. ``accept`` con timeout es lo que permite mirar los dos relojes
#: (idle y die) sin un hilo extra: una sesión de SSM real también los lleva.
ACCEPT_POLL = 0.5

#: Timeout de cada ``recv`` dentro de una conexión reenviada. Corto, porque es
#: lo que deja al hilo de esa conexión volver a mirar ``stall_after`` sin
#: esperar a que el peer se acuerde de enviar algo.
SOCKET_POLL = 0.5

#: Una sesión parada se queda leyendo bytes y tirando, así que ese ``recv`` no
#: necesita volver cada medio segundo: 5s es indistinguible para el cliente que
#: espera, y no gasta CPU en un hilo por conexión colgada.
STALL_POLL = 5.0

#: Cuánto se espera al otro extremo antes de rendirse. Un host mal escrito se
#: responde rápido, con un ``ConnectionRefused`` o con el timeout del sistema
#: operativo; 10s es holgado para loopback y corto para no dejar un puerto local
#: tomado por un destino que no existe.
CONNECT_TIMEOUT = 10.0

#: Backlog del listener. Un pool de MySQL abre unas cuantas conexiones a la vez
#: y el probe las abre y las cierra; 16 sobra y deja margen para que un
#: ``connect()`` no se caiga en el instante en que el supervisor prueba.
BACKLOG = 16

#: Lectura del socket. 64 KiB es el tamaño de página en Windows y en Linux: menos
#: y cada recv es una syscall de más sobre el camino caliente de una query.
CHUNK = 65536

# --- exit codes ---------------------------------------------------------------
#: Cierre limpio: Ctrl+C en la consola. Lo que hace que ``serve_forever`` cierre
#: en vez de reiniciar.
EXIT_OK = 0
#: El puerto local ya lo tenía otro proceso. Distinto de 1 para que el log diga
#: *por qué* terminó y no sólo que terminó.
EXIT_BIND_FAILED = 2
#: Sesión terminada por inactividad. **No es 0 a propósito**: ``serve_forever``
#: reinicia cuando el exit no es 0, y una sesión de Session Manager terminada
#: por el servidor devuelve algo distinto de 0 — con 0, el modo keep-alive del
#: CLI cerraría un túnel que en producción se levanta solo.
EXIT_IDLE = 3
#: ``--die-after``: se cayó en medio de la sesión, sin error escrito.
EXIT_DIED = 4
#: ``accept`` dejó de funcionar. Es un fallo del listener, no de la sesión.
EXIT_ACCEPT_FAILED = 5


class BindFailed(Exception):
    """El puerto local no se pudo tomar. El proceso sale con el motivo a stderr."""


# --- el lado que se spawnea ---------------------------------------------------


def _number(cfg: Config, key: str, default: float) -> float:
    """Un knob de config como float, o el default si no se puede leer.

    Un valor inútil cae al default en vez de tirar. La alternativa —explotar— es
    peor acá: la excepción sale de :func:`argv`, que corre adentro de
    ``ssm_tunnel``, y una excepción distinta de ``ToolUnavailable`` en ese camino
    web es un 500 en vez del 409 que la fila sabe dibujar. Un túnel que levanta
    con el delay equivocado se nota; uno que no levanta, no.
    """
    raw = (cfg.get(key) or "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def argv(request: ForwardRequest) -> list[str]:
    """El comando que levanta un túnel local.

    Los knobs salen de ``Config`` —con override ``YAPPY_*``— y no de
    ``os.environ``: es el mismo accessor que usa el resto de la app, así que un
    ``TUNNEL_LOCAL_IDLE_TIMEOUT`` se puede dejar en el ``env.base`` de un
    ambiente sin que nadie tenga que acordarse de pasarlo por variable.

    ``request.local_port`` sí viene del llamador: es el puerto que contesta el
    probe. ``remote_host`` y ``remote_port``, en cambio, se ignoran. Un host
    remoto de AWS es un nombre que este proceso no resuelve, y ``AWS_PORT`` es
    el 53360 de RDS: el destino real es ``TUNNEL_LOCAL_HOST:TUNNEL_LOCAL_PORT``.
    """
    cfg = Config()
    remote_host = cfg.get("TUNNEL_LOCAL_HOST", DEFAULT_REMOTE_HOST) or DEFAULT_REMOTE_HOST
    remote_port = int(_number(cfg, "TUNNEL_LOCAL_PORT", DEFAULT_REMOTE_PORT))
    target = (cfg.get(f"TUNNEL_LOCAL_TARGET_{request.local_port}") or "").strip()
    if target:
        if ":" in target:
            host, port = target.rsplit(":", 1)
            remote_host = host or remote_host
            remote_port = int(port)
        else:
            remote_port = int(target)
    return [
        sys.executable, "-m", MODULE,
        "--local-port", str(request.local_port),
        "--remote-host", remote_host,
        "--remote-port", str(remote_port),
        "--startup-delay", str(_number(cfg, "TUNNEL_LOCAL_STARTUP_DELAY", DEFAULT_STARTUP_DELAY)),
        "--idle-timeout", str(_number(cfg, "TUNNEL_LOCAL_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT)),
        "--stall-after", str(_number(cfg, "TUNNEL_LOCAL_STALL_AFTER", DEFAULT_STALL_AFTER)),
        "--die-after", str(_number(cfg, "TUNNEL_LOCAL_DIE_AFTER", DEFAULT_DIE_AFTER)),
    ]


# --- el lado que corre --------------------------------------------------------


def _log(message: str) -> None:
    """Una línea al stdout del proceso, que es el log del túnel.

    ASCII a propósito y con ``flush``: este stdout puede ser un pipe, y el
    supervisor lee ``~/.yappy/logs/tunnel-<port>.log`` para explicar un fallo.
    Un ``print`` sin flush en un pipe se queda en el buffer, y el log queda
    vacío justo cuando más lo necesita. Y la consola de Windows es cp1252, así
    que un tilde parte la línea con un error de ``charmap``.
    """
    print(f"local-forwarder: {message}", flush=True)


def _configure_reuse(sock: socket.socket) -> None:
    """La política de reuso de dirección, que en Windows es lo opuesto a POSIX.

    En POSIX ``SO_REUSEADDR`` exime **solamente** del ``TIME_WAIT``, y hace
    falta: con ``--idle-timeout`` en 60s el listener se cierra y se reabre
    constantemente, y sin esta opción el siguiente arranque sobre el mismo
    ``local_port`` falla con ``EADDRINUSE`` hasta que expire el ``TIME_WAIT``.

    En Windows ``SO_REUSEADDR`` no exime de un ``TIME_WAIT``: autoriza a **dos
    sockets a hacer bind del mismo par addr:puerto**, y el último gana. Eso no
    es una optimización, es permitir el robo de puertos — y dejaría pasar
    justamente el comportamiento que este forwarder existe para reproducir: el
    bind fallido se volvería un bind exitoso que le roba el puerto al dueño
    real, el probe vería el mismo puerto respondiendo y nadie se enteraría de
    que hay dos túneles. La inversa documentada de esa opción es
    ``SO_EXCLUSIVEADDRUSE``, que es la que se pone acá. Sin ella el default de
    Windows ya rechaza el segundo bind, pero ponerlo explícito deja la intención
    escrita en el código en vez de heredada.
    """
    if sys.platform == "win32":
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def _listen(local_port: int) -> socket.socket:
    """El listener local, o :class:`BindFailed` con el motivo.

    El bind va **después** de ``--startup-delay`` y no antes. Bindear antes
    haría que el puerto "existiera" sin contestar —un ``connect()`` recibe
    ``ECONNREFUSED`` en vez de hacer cola— y ese es un estado que ningún
    ``session-manager-plugin`` tiene: o no hay puerto, o hay uno que responde.
    La consecuencia aceptada es que un bind fallido se reporta
    ``startup_delay`` segundos tarde, que contra los 30s de
    ``readiness.DEFAULT_TIMEOUT`` y los 30s de ``connection._wait_for_port`` no
    es una espera perceptible.

    Un pre-chequeo del puerto *antes* de la espera haría el fallo instantáneo, y
    por eso no está: el que decide es el bind real, y un chequeo previo puede
    ver un conflicto que se resuelve durante la espera —el caso real es el
    ``prune_dead_on_port`` de ``actions.ensure_port_free`` limpiando un
    fantasma— y con eso negaría un arranque que iba a funcionar.

    El timeout del listener va acá y no en :func:`serve` porque es lo que define
    "acepta conexiones": el loop lo ajusta antes de cada ``accept`` a lo que
    falta para el próximo reloj.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _configure_reuse(sock)
    try:
        sock.bind(("127.0.0.1", local_port))
    except OSError as exc:
        sock.close()
        raise BindFailed(
            f"127.0.0.1:{local_port} is already in use ({exc}). Nothing is "
            "listening on behalf of this tunnel: stop whatever holds the port "
            "and start it again."
        ) from exc
    sock.listen(BACKLOG)
    sock.settimeout(ACCEPT_POLL)  # el loop lo ajusta a lo que falta para el próximo reloj
    return sock


class _Activity:
    """Cuándo se movió el último byte, para el idle de la sesión.

    Un solo reloj para toda la sesión y no uno por conexión, porque el idle de
    Session Manager es de la *sesión*: cinco conexiones abiertas en silencio
    durante medio minuto siguen siendo medio minuto sin tráfico.

    ``touch`` se llama al conectar y en cada chunk reenviado, en las dos
    direcciones. Lo que cuenta es "hubo tráfico", no "el cliente está
    conectado": una conexión que el cliente abrió y no volvió a tocar es
    exactamente el caso que mata una sesión real.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def idle_for(self) -> float:
        with self._lock:
            return time.monotonic() - self._last


def _discard(source: socket.socket) -> bytes:
    """Lee y tira, con un timeout largo: el hilo va a quedar acá parado."""
    source.settimeout(STALL_POLL)
    return source.recv(CHUNK)


def _pump(
    source: socket.socket,
    dest: socket.socket,
    activity: _Activity,
    stall_at: float | None,
) -> None:
    """Reenvía ``source`` a ``dest`` hasta que un lado cierra.

    ``stall_at`` es el momento en que el canal de datos deja de existir, y no un
    flag: desde ahí el socket sigue abierto y se sigue leyendo **desechando** lo
    que llega, para que el cliente quede bloqueado esperando una respuesta que no
    va a llegar. Cerrar el socket, en cambio, le daría un FIN al cliente, y un
    FIN es una respuesta: dice "el túnel se cayó", que es un diagnóstico distinto
    del que se está probando —"abrió el puerto y no contestó nada"—. Por eso el
    hilo queda parqueado acá hasta que muera el proceso, en vez de hacer
    ``return``.

    ``stall_at`` es un instante y no un flag por conexión: en el mundo real el
    canal de datos no vuelve, así que las conexiones que se abran después de ese
    momento también quedan mudas.
    """
    stalled = False
    while True:
        if not stalled and stall_at is not None and time.monotonic() >= stall_at:
            stalled = True
        try:
            chunk = _discard(source) if stalled else source.recv(CHUNK)
        except TimeoutError:
            # No es EOF: es que en este medio segundo no pasó nada. Un ``return``
            # acá cerraría conexiones que el peer sólo tiene pausadas, que es la
            # mitad de las conexiones de un pool de MySQL.
            continue
        except OSError:
            return
        if not chunk:
            break
        if stalled:
            continue
        activity.touch()
        try:
            dest.sendall(chunk)
        except OSError:
            return
    try:
        dest.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _handle(
    client: socket.socket,
    remote: tuple[str, int],
    activity: _Activity,
    stall_at: float | None,
) -> None:
    """Una conexión local contra el destino real, reenviando en los dos sentidos."""
    try:
        upstream = socket.create_connection(remote, timeout=CONNECT_TIMEOUT)
    except OSError as exc:
        _log(f"cannot reach {remote[0]}:{remote[1]}: {exc}")
        client.close()
        return
    activity.touch()
    upstream.settimeout(SOCKET_POLL)
    client.settimeout(SOCKET_POLL)
    # Un hilo por dirección. El que corre en esta thread es el del cliente: es el
    # que termina cuando el cliente cierra, y en ese momento hay que soltar los
    # dos sockets o el otro queda bloqueado en un recv que nunca devuelve. Ambos
    # son daemon, así que un exit del proceso los mata igual.
    downhill = threading.Thread(
        target=_pump,
        args=(upstream, client, activity, stall_at),
        name="local-forwarder-up",
        daemon=True,
    )
    downhill.start()
    _pump(client, upstream, activity, stall_at)
    for sock in (client, upstream):
        try:
            sock.close()
        except OSError:
            pass


def serve(args: argparse.Namespace) -> int:
    """El loop del forwarder. Devuelve el código de salida.

    Los tres relojes se cuentan desde el **bind**, no desde que arrancó el
    proceso: antes del bind todavía no hay sesión, y un ``--die-after`` que
    heredara los 3s del ``--startup-delay`` mataría el túnel antes de que el
    supervisor llegue a mirarlo, que no es el caso que se quiere probar.
    """
    remote = (args.remote_host, args.remote_port)
    _log(f"starting: 127.0.0.1:{args.local_port} -> {remote[0]}:{remote[1]}")
    if args.startup_delay > 0:
        _log(f"session not established yet ({args.startup_delay:g}s)")
        time.sleep(args.startup_delay)

    listener = _listen(args.local_port)
    bound_at = time.monotonic()
    stall_at = bound_at + args.stall_after if args.stall_after > 0 else None
    die_at = bound_at + args.die_after if args.die_after > 0 else None
    activity = _Activity()
    _log(f"listening on 127.0.0.1:{args.local_port}")

    while True:
        if die_at is not None and time.monotonic() >= die_at:
            _log(f"data channel lost after {args.die_after:g}s")
            return EXIT_DIED
        if args.idle_timeout > 0 and activity.idle_for() >= args.idle_timeout:
            _log(f"session terminated after {args.idle_timeout:g}s of inactivity")
            return EXIT_IDLE
        # El accept no se queda más tiempo bloqueado que lo que falta para el
        # próximo reloj. Con un poll fijo de medio segundo, un ``--die-after 0.2``
        # se cumpliría medio segundo tarde y el knob mentiría — y en un knob que
        # existe para provocar una falla, medio segundo de diferencia es la
        # diferencia entre el caso que se quiere ver y otro.
        wait = ACCEPT_POLL
        if die_at is not None:
            wait = min(wait, die_at - time.monotonic())
        if args.idle_timeout > 0:
            wait = min(wait, args.idle_timeout - activity.idle_for())
        listener.settimeout(max(0.01, wait))
        try:
            client, _ = listener.accept()
        except TimeoutError:
            continue
        except OSError as exc:
            print(f"local-forwarder: accept failed: {exc}", file=sys.stderr, flush=True)
            return EXIT_ACCEPT_FAILED
        threading.Thread(
            target=_handle,
            args=(client, remote, activity, stall_at),
            name="local-forwarder-conn",
            daemon=True,
        ).start()


def _parse_args(args: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=MODULE,
        description="A local stand-in for an SSM port-forwarding session.",
    )
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT)
    parser.add_argument("--startup-delay", type=float, default=DEFAULT_STARTUP_DELAY)
    parser.add_argument(
        "--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT,
        help="seconds with no bytes moved before the session ends; 0 = never",
    )
    parser.add_argument(
        "--stall-after", type=float, default=DEFAULT_STALL_AFTER,
        help="seconds after binding at which the data channel stops moving bytes; 0 = never",
    )
    parser.add_argument(
        "--die-after", type=float, default=DEFAULT_DIE_AFTER,
        help="seconds after binding at which the process exits mid-session; 0 = never",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """Punto de entrada del proceso. ``args`` es inyectable para los tests."""
    parsed = _parse_args(args)
    try:
        return serve(parsed)
    except BindFailed as exc:
        # El único refusal posible de este proceso. Va a stderr con el motivo y
        # sale ya: quedarse escuchando la espera cuando el puerto ya lo tenía
        # otro es exactamente lo que `processes.py:395-398` describe.
        print(f"local-forwarder: {exc}", file=sys.stderr, flush=True)
        return EXIT_BIND_FAILED
    except KeyboardInterrupt:
        _log("interrupted")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
