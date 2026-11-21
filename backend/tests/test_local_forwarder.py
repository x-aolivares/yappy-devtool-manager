"""El forwarder local, corriendo de verdad: un proceso, un pid, un puerto.

Cada test de acá levanta ``python -m ...local_forwarder`` de verdad y le habla por
TCP de verdad, contra un echo server real y con puertos efímeros. No hay mocks y
no los puede haber: lo que se está probando es que **el puerto no escucha
todavía**, que un bind tomado sale **rápido**, que una sesión sin tráfico **se
muere sola**, y que dos conexiones a la vez funcionan. Un ``socket.socket()`` en
el mismo proceso probaría la función de reenvío, no el túnel, y del túnel lo que
el tracker y el guard de ``taskkill /t`` necesitan es el pid.

El otro extremo —el que el forwarder no controla— es un echo server de 25 líneas
en vez de un MySQL: la suite no puede depender de que haya un contenedor de
Docker arriba, y en bytes de ida y vuelta un echo y un saludo de MySQL son el
mismo problema. El probe real tiene su propio test, más abajo.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import closing

import pytest

from yappy_library.adapters.forwarding import local_forwarder

#: Cuánto se espera por una cosa que debería pasar ya. Generoso porque la suite
#: corre en Windows con el antivirus mirando los procesos nuevos, y un límite
#: corto de más produce un fallo que no es del código bajo prueba.
QUICK = 5.0
#: Cuánto se espera por lo que tiene que ser rápido **a propósito**. Si un bind
#: fallido tardara esto en salir, estaría "funcionando" y no fallando, que es
#: justo el bug de ``processes.py:395-398``.
PROMPT = 10.0


# --- la pareja: un destino real y un forwarder real ----------------------------


class _EchoServer:
    """Un destino que saluda al conectar y después devuelve lo que recibe.

    Un hilo por conexión. El saludo va una vez por conexión y no por chunk: es
    lo que hace MySQL, y prefijar cada chunk haría que un payload de 200 KiB
    —que llega en varios segmentos TCP— saliera con un ``echo:`` intercalado cada
    64 KiB, o sea, un test que mide el reenvío del forwarder y en realidad
    mide el framing del servidor de prueba.
    """

    PREFIX = b"echo:"

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._socket.settimeout(0.3)
        self.port = self._socket.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        try:
            client.sendall(self.PREFIX)
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    return
                client.sendall(chunk)
        except OSError:
            pass
        finally:
            client.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def echo():
    server = _EchoServer()
    yield server
    server.close()


def _free_port() -> int:
    """Un puerto que nadie está tomando ahora.

    Cierra el socket antes de devolverlo, así que queda una ventana en la que
    otro proceso puede ganar la carrera. Aceptable: el rango efímero son 16k
    puertos y esta suite abre dos a la vez.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Forwarder:
    """Un ``python -m`` del forwarder, con su pid y su salida capturada."""

    def __init__(self, **flags):
        self.port = flags.pop("local_port")
        argv = [
            sys.executable, "-m", local_forwarder.MODULE,
            "--local-port", str(self.port),
            "--remote-host", flags.pop("remote_host", "127.0.0.1"),
            "--remote-port", str(flags.pop("remote_port", 3306)),
            "--startup-delay", str(flags.pop("startup_delay", 0.0)),
            "--idle-timeout", str(flags.pop("idle_timeout", 0.0)),
            "--stall-after", str(flags.pop("stall_after", 0.0)),
            "--die-after", str(flags.pop("die_after", 0.0)),
        ]
        assert not flags, f"knobs que el runner no tiene: {sorted(flags)}"
        self._proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

    @property
    def pid(self) -> int:
        return self._proc.pid

    def running(self) -> bool:
        return self._proc.poll() is None

    def wait(self, timeout: float) -> int:
        return self._proc.wait(timeout=timeout)

    def stop(self) -> None:
        """Pararlo como lo para la app: matando el árbol del pid.

        Es el mismo ``taskkill /pid .. /t /f`` de ``BaseCommand._kill_pid``, y es
        la mitad de lo que este archivo prueba: que el forwarder sea un proceso
        de host del que ese comando se puede servir.
        """
        subprocess.run(
            ["taskkill", "/pid", str(self._proc.pid), "/t", "/f"]
            if sys.platform == "win32"
            else ["kill", "-9", str(self._proc.pid)],
            capture_output=True,
        )
        self._proc.wait(timeout=PROMPT)

    def log(self) -> str:
        out, err = self._proc.communicate(timeout=PROMPT)
        return (out or "") + (err or "")


@pytest.fixture
def forwarder_factory():
    """Todo forwarder que abra un test se para al final, se haya caído o no.

    Sin esto, un test que falla deja un proceso con el puerto tomado y el
    siguiente que usa un puerto libre igual falla por un motivo que no es suyo.
    """
    started: list[_Forwarder] = []

    def make(**flags) -> _Forwarder:
        forwarder = _Forwarder(**flags)
        started.append(forwarder)
        return forwarder

    yield make

    for forwarder in started:
        if forwarder.running():
            forwarder.stop()


def _listening(port: int, timeout: float = 0.4) -> bool:
    """Si algo acepta una conexión en ``port`` ahora mismo.

    Un ``connect`` a un puerto cerrado falla enseguida, así que un timeout corto
    no confunde "nadie escucha" con "tardó". Es el mismo criterio que usa
    ``connection.mysql_greeting``.
    """
    try:
        with closing(socket.create_connection(("127.0.0.1", port), timeout=timeout)):
            return True
    except OSError:
        return False


def _wait_listening(port: int, timeout: float = QUICK) -> float:
    """Espera a que el puerto conteste. Devuelve cuánto tardó, en segundos."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if _listening(port):
            return time.monotonic() - started
        time.sleep(0.05)
    raise AssertionError(f"nothing ever listened on {port} within {timeout:g}s")


def _echo_through(port: int, payload: bytes, timeout: float = QUICK) -> bytes:
    with closing(socket.create_connection(("127.0.0.1", port), timeout=timeout)) as client:
        client.settimeout(timeout)
        client.sendall(payload)
        received = b""
        expected = len(_EchoServer.PREFIX) + len(payload)
        while len(received) < expected:
            chunk = client.recv(expected - len(received))
            if not chunk:
                break
            received += chunk
        return received


# --- el bind: el puerto no existe hasta que la sesión existe -------------------


def test_el_puerto_no_escucha_antes_del_startup_delay(forwarder_factory):
    """La razón de ``connection._wait_for_port(port, timeout=30.0)``.

    Un forwarder que escucha en el instante del spawn hace que todo probe que
    consulta el puerto dé un falso "levantando", y los 30s de espera que existen
    para cubrir los segundos que tarda una sesión real pasan a ser código muerto
    que nadie sabe explicar.
    """
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, startup_delay=2.0)

    time.sleep(0.8)
    assert not _listening(port), "escuchó antes de que la sesión se estableciera"
    assert forwarder.running()

    took = _wait_listening(port, timeout=QUICK)
    assert took >= 1.0, f"escuchó en {took:.2f}s, antes de los 2s de startup_delay"


def test_un_puerto_ya_tomado_sale_rapido_y_dice_por_que(echo, forwarder_factory):
    """El bug de ``processes.py:395-398``: un bind fallido tiene que *parecer* un
    bind fallido. Un forwarder que espera el bind callado se ve igual que uno que
    todavía está levantando, y eso es una request esperando para siempre contra
    un túnel que nunca iba a andar.
    """
    port = _free_port()
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen(1)
        forwarder = forwarder_factory(
            local_port=port, remote_port=echo.port, startup_delay=0.0
        )

        started = time.monotonic()
        code = forwarder.wait(timeout=PROMPT)
        took = time.monotonic() - started

    assert code == local_forwarder.EXIT_BIND_FAILED
    assert code != 0
    assert took < 5.0, f"tardó {took:.1f}s en decir que no podía: eso es 'todavía levantando'"
    log = forwarder.log()
    assert str(port) in log
    assert "in use" in log


def test_el_bind_fallido_no_deja_nada_escuchando(echo, forwarder_factory):
    """El refusal tiene que ser un refusal: nada queda tomando el puerto atrás.

    Sin esto, el siguiente túnel que se levanta sobre el mismo ``local_port``
    hereda un zombi que nadie puede explicar, y ``ensure_port_free`` —que sólo
    mira entradas vivas del tracker— no lo ve.
    """
    port = _free_port()
    squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    squatter.bind(("127.0.0.1", port))
    squatter.listen(1)
    try:
        forwarder = forwarder_factory(
            local_port=port, remote_port=echo.port, startup_delay=0.0
        )
        assert forwarder.wait(timeout=PROMPT) == local_forwarder.EXIT_BIND_FAILED
    finally:
        squatter.close()

    # Ya no hay dueño: si el forwarder hubiera dejado un socket abierto en
    # nombre de un túnel que no existe, el puerto seguiría tomado.
    assert not _listening(port)


# --- el reenvío ---------------------------------------------------------------


def test_los_bytes_van_en_las_dos_direcciones(echo, forwarder_factory):
    """Ida y vuelta sobre el mismo socket: si el saludo volvió, cruzó el túnel
    en los dos sentidos."""
    port = _free_port()
    forwarder_factory(local_port=port, remote_port=echo.port)

    assert _echo_through(port, b"SELECT 1") == b"echo:SELECT 1"


def test_un_payload_mas_grande_que_el_chunk(echo, forwarder_factory):
    """64 KiB por ``recv``, y el loop tiene que seguir. Un forwarder que
    reenvía un solo chunk por conexión no corta una query de 200 KiB: la deja
    colgada y el cliente no se da cuenta hasta que vence el timeout de la
    sesión."""
    port = _free_port()
    forwarder_factory(local_port=port, remote_port=echo.port)
    payload = bytes(range(256)) * 800  # 200 KiB, mucho más que un chunk

    assert _echo_through(port, payload, timeout=QUICK) == echo.PREFIX + payload


def test_dos_conexiones_al_mismo_tiempo(echo, forwarder_factory):
    """Un MySQL con pool abre más de una conexión, y un forwarder que acepta una
    sola a la vez serializa todo lo que hay detrás. Las dos conversaciones se
    abren antes de que ninguna termine, así que si el accept fuera uno a uno la
    segunda esperaría a la primera.
    """
    port = _free_port()
    forwarder_factory(local_port=port, remote_port=echo.port)
    _wait_listening(port)

    replies: dict[int, bytes] = {}

    def talk(index: int) -> None:
        try:
            replies[index] = _echo_through(port, f"conexion-{index}".encode())
        except OSError as exc:  # pragma: no cover - se muestra en el assert de abajo
            replies[index] = repr(exc).encode()

    threads = [threading.Thread(target=talk, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=QUICK)

    assert replies == {0: b"echo:conexion-0", 1: b"echo:conexion-1"}


def test_un_destino_que_no_existe_no_tira_la_sesion(forwarder_factory):
    """Si el otro extremo no está, la conexión local muere; el túnel sigue.

    Un puerto local que acepta y no contesta nunca es *otra* cosa, y es el
    síntoma que el probe tiene que poder distinguir de "se cayó". Además, una
    sola conexión fallida no puede matar la sesión: con un pool de MySQL, una
    query a un destino caído es un error, no un túnel muerto.
    """
    port = _free_port()
    nowhere = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=nowhere)
    _wait_listening(port)

    with closing(socket.create_connection(("127.0.0.1", port), timeout=QUICK)) as client:
        client.settimeout(QUICK)
        assert client.recv(16) == b"", "una conexión sin destino se cierra, no cuelga"

    assert forwarder.running()


# --- los relojes ---------------------------------------------------------------


def test_la_sesion_se_muere_sola_si_no_mueve_un_byte(echo, forwarder_factory):
    """El comportamiento que más cuesta reproducir sin AWS.

    Session Manager termina las sesiones inactivas, y por eso una fila pasa de
    ``up`` a ``dead`` sin error y sin que nadie haya tocado nada. El código de
    salida no es 0 a propósito: ``serve_forever`` reinicia cuando el exit no es
    0, que es lo que pasa con una sesión real que el servidor corta.
    """
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port, idle_timeout=1.0)
    _wait_listening(port)

    # Una conexión abierta y muda. Conectar solo no alcanza: el handshake
    # cuenta como tráfico, igual que en una sesión real.
    with closing(socket.create_connection(("127.0.0.1", port), timeout=QUICK)) as idle:
        idle.settimeout(QUICK)
        code = forwarder.wait(timeout=PROMPT)
        # Al morir el proceso, el socket que tenía abierto se va con él. En
        # Windows eso es un RST y no un FIN limpio, y la diferencia importa: el
        # probe tiene que ver las dos como "el túnel no está", y lo que no
        # puede es quedarse esperando.
        with pytest.raises(OSError):
            assert idle.recv(16) == b""

    assert code == local_forwarder.EXIT_IDLE
    assert code != 0


def test_el_idle_no_es_un_reloj_desde_el_arranque(echo, forwarder_factory):
    """``--idle-timeout`` cuenta sin tráfico, no desde que arrancó el proceso.

    Un túnel que alguien dejó abierto toda la mañana y que se usa cada media hora
    no es una sesión inactiva: es una sesión en uso. Con un reloj de pared el
    proceso se muere a los 60s aunque alguien lo esté usando, y la fila pasa de
    ``up`` a ``dead`` mientras la query responde.
    """
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port, idle_timeout=2.0)
    _wait_listening(port)

    for _ in range(3):
        time.sleep(0.4)
        assert _echo_through(port, b"ping") == b"echo:ping"
        assert forwarder.running(), "el tráfico no reinicia un idle, lo pospone"

    assert forwarder.wait(timeout=PROMPT) == local_forwarder.EXIT_IDLE


def test_idle_timeout_cero_no_mata_nunca(echo, forwarder_factory):
    """0 es "nunca", y no "todo el rato": es lo que necesita una sesión que
    alguien sostiene con un ``serve_forever``."""
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port, idle_timeout=0.0)
    _wait_listening(port)

    time.sleep(1.5)
    assert forwarder.running()
    assert _echo_through(port, b"sigue") == b"echo:sigue"


def test_el_canal_de_datos_se_muere_sin_matarel_proceso(echo, forwarder_factory):
    """``--stall-after`` levanta un puerto que no contesta y deja el proceso vivo.

    Es el caso del veredicto "abrió el puerto pero no respondió" de
    ``supervisor.py``, y necesita dos cosas para ser ese caso y no otro: que no
    llegue ni un byte ni un FIN —un FIN dice "el túnel se cayó", que es otro
    diagnóstico— y que el proceso siga trackeado.
    """
    port = _free_port()
    forwarder = forwarder_factory(
        local_port=port, remote_port=echo.port, stall_after=1.0, idle_timeout=0.0
    )
    _wait_listening(port)

    # Más allá del stall_after, que se cuenta desde el bind y no desde el spawn.
    time.sleep(2.0)
    with closing(socket.create_connection(("127.0.0.1", port), timeout=QUICK)) as client:
        client.settimeout(1.5)
        client.sendall(b"hola")
        with pytest.raises(TimeoutError):
            client.recv(64)

    assert forwarder.running(), "una sesión parada sigue siendo una sesión"


def test_el_proceso_se_muere_en_medio_de_la_sesion(echo, forwarder_factory):
    """``--die-after`` deja un proceso trackeado y muerto, que es lo que
    ``reconcile()`` limpia al arrancar el server y lo que ``prune_dead_on_port``
    borra antes de un start."""
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port, die_after=1.0)
    _wait_listening(port)

    assert forwarder.wait(timeout=PROMPT) == local_forwarder.EXIT_DIED
    assert not _listening(port), "un proceso muerto no puede seguir con el puerto"


# --- el proceso es un proceso --------------------------------------------------


def test_no_hay_ruido_de_runpy_en_el_log_del_tunel(echo, forwarder_factory):
    """El log del túnel es el archivo que el supervisor lee para explicar un
    fallo, así que una línea de ruido en cada túnel local le arruina la lectura
    a quien lo está depurando.

    El ruido aparece solo si el paquete padre importa el módulo ejecutable:
    ``runpy`` importa el paquete, y con él el módulo, antes de ejecutarlo como
    ``__main__``. Son dos copias del mismo código en memoria y un
    ``RuntimeWarning`` en stderr.
    """
    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port)
    _wait_listening(port)
    forwarder.stop()

    assert "RuntimeWarning" not in forwarder.log()


def test_el_guard_de_identidad_lo_reconoce(echo, forwarder_factory):
    """``track_process`` graba el ``exe`` y el ``create_time`` que le lee al
    sistema operativo, y el guard de ``kill_ssm`` los usa para no matar a un pid
    reciclado. Un thread no tiene ninguno de los dos, así que el simulador
    tendría que ser un proceso de host o no sería nada.
    """
    from yappy_library.adapters import process_identity

    port = _free_port()
    forwarder = forwarder_factory(local_port=port, remote_port=echo.port)
    _wait_listening(port)

    identity = process_identity.identity(forwarder.pid)
    assert identity is not None, "el proceso del forwarder no está vivo"
    expected = os.path.basename(sys.executable)
    if sys.platform == "win32":
        expected = expected.lower()
    assert identity.exe == expected, (
        f"el guard compararía {identity.exe!r} contra {expected!r} y no lo reconhecería"
    )
    assert identity.create_time


# --- contra el MySQL de verdad ------------------------------------------------


@pytest.mark.skipif(
    not _listening(3306), reason="no hay un MySQL escuchando en 127.0.0.1:3306"
)
def test_el_probe_real_de_la_app_pasa_por_el_forwarder():
    """La prueba de que esto es un túnel y no un ``socket`` con eco.

    ``readiness.probe_mysql`` no acepta un ``connect()`` como respuesta: lee el
    saludo de MySQL. Si el reenvío funciona de verdad, el saludo del contenedor
    aparece por el puerto local; si el forwarder mintiera, aparecería un string
    cualquiera o nada. Y un ``pytest.skip`` en vez de un fallo cuando no hay
    Docker: la suite no puede depender de un contenedor.
    """
    from yappy_library.application.workflow.readiness import probe_mysql

    port = _free_port()
    forwarder = _Forwarder(
        local_port=port, remote_host="127.0.0.1", remote_port=3306, startup_delay=0.0
    )
    try:
        _wait_listening(port)
        assert probe_mysql(port, timeout=2.0) is True
    finally:
        if forwarder.running():
            forwarder.stop()
