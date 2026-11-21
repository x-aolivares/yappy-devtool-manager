"""Cuál forwarder levanta un túnel, y que el de AWS no haya cambiado.

``BaseCommand.ssm_tunnel`` es la **única** dependencia de AWS que tiene el código
de túneles, y el movimiento de su argv a :mod:`.adapters.forwarding.ssm_forwarder`
es el único cambio que puede romper un túnel en la máquina de alguien que no
puede correr un test antes de levantar el web. Así que el argv está pineado acá
como literal, no releído del módulo: releído, el test afirmaría que el código
acuerda consigo mismo, que es exactamente lo que no importa.

Lo que además se afirma acá:

- los dos forwarders satisfacen el puerto estructuralmente, así que el que se
  elige por config es del mismo tipo que el de producción;
- un nombre que no existe cae en ``ssm`` y no explota;
- ``ssm_tunnel`` con su default sigue emitting el comando de AWS, con el mismo
  sink y el mismo ``track_process``.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import yappy_library.adapters.processes as base
from yappy_library.adapters import process_tracker
from yappy_library.adapters.forwarding import DEFAULT_FORWARDER, forwarder_for
from yappy_library.adapters.forwarding import local_forwarder, ssm_forwarder
from yappy_library.ports.port_forwarder import ForwardRequest, PortForwarder

# El request de siempre, con los tres números que el ejemplo de arriba usa.
_REQUEST = ForwardRequest(
    instance="i-0123456789",
    region="us-west-2",
    profile="base-profile",
    local_port=8402,
    remote_port=9000,
    remote_host="core.dev.yappycloud.com",
)


@pytest.fixture
def aws_cli(monkeypatch):
    """Que ``_aws_cmd`` resuelva al binario sin preguntar por el PATH.

    Se monkeypatchea el **atributo del módulo**, no el nombre importado: por eso
    :func:`ssm_forwarder.argv` lo busca adentro de la función, y un
    ``from ... import _aws_cmd`` arriba del archivo dejaría este test mintiendo
    sobre lo que el código real ejecuta.
    """
    monkeypatch.setattr(base, "_aws_cmd", lambda: ["aws"])
    return ["aws"]


# --- el puerto, y que los dos lo cumplan --------------------------------------


@pytest.mark.parametrize(
    "module", [ssm_forwarder, local_forwarder], ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)
def test_cada_forwarder_cumple_el_puerto(module):
    """Estructural, no por herencia: el contrato es "tené estos miembros"."""
    assert isinstance(module, PortForwarder)


def test_el_puerto_no_pide_mas_que_el_argv():
    """Un puerto con un solo miembro es lo que hace que no haya dónde meter un
    requerimiento nuevo. Si alguien le agrega un ``stop()``, esto falla y la
    discusión tiene que volver a tener lugar a propósito."""
    assert set(PortForwarder.__protocol_attrs__) == {"argv"}


# --- el argv de ssm, pineado ---------------------------------------------------

#: El comando exacto, flag por flag y con el JSON exacto. Escrito a mano y no
#: con un ``json.dumps`` del lado del test: si el módulo y el test construyen el
#: JSON con el mismo código, un cambio de orden de claves los mueve a los dos y
#: el test sigue verde.
SSM_ARGV_REMOTE_HOST = [
    "aws", "ssm", "start-session",
    "--target", "i-0123456789",
    "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
    "--parameters", '{"portNumber": ["9000"], "host": ["core.dev.yappycloud.com"], "localPortNumber": ["8402"]}',
    "--profile", "base-profile",
    "--region", "us-west-2",
]

SSM_ARGV_LOCAL = [
    "aws", "ssm", "start-session",
    "--target", "i-0123456789",
    "--document-name", "AWS-StartPortForwardingSession",
    "--parameters", '{"portNumber": ["9000"], "localPortNumber": ["8402"]}',
    "--profile", "base-profile",
    "--region", "us-west-2",
]


def test_ssm_arma_exactamente_el_comando_de_hoy(aws_cli):
    assert ssm_forwarder.argv(_REQUEST) == SSM_ARGV_REMOTE_HOST


def test_sin_host_remoto_es_otro_documento(aws_cli):
    """Un host vacío y un ``None`` quieren decir lo mismo, y el texto del JSON
    también: la clave ``host`` no aparece. Un documento distinto acá levanta un
    túnel que no llega a ningún lado."""
    assert ssm_forwarder.argv(
        ForwardRequest(
            instance="i-0123456789", region="us-west-2", profile="base-profile",
            local_port=8402, remote_port=9000,
        )
    ) == SSM_ARGV_LOCAL


def test_un_host_remoto_vacio_es_sin_host_remoto(aws_cli):
    """La condición es truthiness, no ``is not None``, porque así era y un
    ``""`` explícito significa "sin host"."""
    assert ssm_forwarder.document_name("") == ssm_forwarder.DOC_LOCAL


def test_el_orden_de_las_claves_del_json_es_parte_del_comando(aws_cli):
    """``json.dumps`` respeta el orden de inserción, y ese texto es lo que el
    tunnel log muestra. Dos JSON que parsean igual no son el mismo comando."""
    params = json.loads(ssm_forwarder.argv(_REQUEST)[8])
    assert list(params) == ["portNumber", "host", "localPortNumber"]
    assert list(json.loads(ssm_forwarder.argv(
        ForwardRequest(instance="i", region="r", profile="p", local_port=1, remote_port=2)
    )[8])) == ["portNumber", "localPortNumber"]


def test_el_prefijo_del_binario_sigue_siendo_el_que_resuelve_aws_cmd(monkeypatch):
    """``python -m awscli`` es una instalación válida: una máquina sin ``aws`` en
    el PATH tiene que andar igual, y por eso el prefijo no está hardcodeado."""
    monkeypatch.setattr(base, "_aws_cmd", lambda: ["C:/py", "-m", "awscli"])
    assert ssm_forwarder.argv(_REQUEST)[:4] == ["C:/py", "-m", "awscli", "ssm"]


# --- la selección --------------------------------------------------------------


def test_el_default_es_ssm(monkeypatch):
    """Sin la clave, ``ssm``. Cambiar el default cambiaría el comportamiento de
    toda máquina que no tocó la config, y eso se descubre en la pantalla de
    alguien que no tiene ni idea de esto."""
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)
    assert DEFAULT_FORWARDER == "ssm"
    assert forwarder_for() is forwarder_for("ssm")


def test_la_config_elige_el_forwarder(monkeypatch):
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "local")
    assert forwarder_for() is local_forwarder


def test_la_config_no_se_cachea(monkeypatch):
    """Un ``lru_cache`` haría que cambiar la variable en la terminal no cambie
    nada en el web server que ya está corriendo."""
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "local")
    assert forwarder_for() is local_forwarder
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "ssm")
    assert forwarder_for() is ssm_forwarder


def test_un_nombre_que_no_existe_cae_en_ssm(monkeypatch):
    """Como ``readiness.probe_for``: un typo tiene que verse como algo que no
    funcionó, no como un error en la fila que el developer clickeó."""
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)
    assert forwarder_for("loacl") is ssm_forwarder


def test_un_nombre_en_mayúsculas_sirve(monkeypatch):
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "LOCAL")
    assert forwarder_for() is local_forwarder


def test_un_config_con_valor_vacio_cae_en_ssm(monkeypatch):
    """Un ``TUNNEL_FORWARDER=`` en el env.base es un typo que se querer resolver,
    no una instrucción de no tener forwarder."""
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "")
    assert forwarder_for() is ssm_forwarder


# --- el argv de local ----------------------------------------------------------


def test_local_arma_un_proceso_de_python_y_no_un_thread(monkeypatch):
    """Un thread no tiene pid, ni exe, ni lugar en el tracker: es el proceso lo
    que el guard de ``taskkill /t`` y ``still_running`` tienen que poder
    identificar. Un ``sys.executable -m`` es lo que lo garantiza."""
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "local")
    argv = forwarder_for().argv(_REQUEST)

    assert argv[1:3] == ["-m", local_forwarder.MODULE]
    assert argv[0] == local_forwarder.sys.executable
    assert argv[argv.index("--local-port") + 1] == "8402"


def test_local_ignora_el_host_remoto_de_aws(monkeypatch):
    """``AWS_PORT`` es el 53360 de RDS y ``AWS_HOST`` un nombre que no resuelve:
    el destino del forwarder local sale de su propia config."""
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "local")
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_PORT", "3307")

    argv = local_forwarder.argv(_REQUEST)

    assert argv[argv.index("--remote-port") + 1] == "3307"
    assert "core.dev.yappycloud.com" not in argv


def test_local_toma_los_knobs_de_la_config(monkeypatch):
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_HOST", "localhost")
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_STARTUP_DELAY", "0.25")
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_IDLE_TIMEOUT", "5")
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_STALL_AFTER", "2")
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_DIE_AFTER", "9")

    argv = local_forwarder.argv(_REQUEST)

    def read(flag: str) -> str:
        return argv[argv.index(flag) + 1]

    assert read("--remote-host") == "localhost"
    assert read("--startup-delay") == "0.25"
    assert read("--idle-timeout") == "5.0"
    assert read("--stall-after") == "2.0"
    assert read("--die-after") == "9.0"


def test_un_knob_inutilizable_cae_al_default(monkeypatch):
    """Un ``TUNNEL_LOCAL_IDLE_TIMEOUT=ya`` no puede romper el arranque: la
    excepción sale adentro de ``ssm_tunnel``, que es un camino web, y ahí lo que
    se necesita es un 409 con motivo, no un 500."""
    monkeypatch.setenv("YAPPY_TUNNEL_LOCAL_IDLE_TIMEOUT", "ya")
    argv = local_forwarder.argv(_REQUEST)
    assert argv[argv.index("--idle-timeout") + 1] == str(local_forwarder.DEFAULT_IDLE_TIMEOUT)


# --- ssm_tunnel: la firma y el comportamiento por defecto ----------------------


@pytest.fixture
def spawned(monkeypatch):
    """Captura el argv y el sink de cada ``Popen`` que hace ``ssm_tunnel``.

    Se intercepta ``Popen`` y no ``ssm_tunnel``: el punto es que el comando que
    sale sea el mismo en las dos rutas.
    """
    seen: list[dict] = []

    def record(cmd, **kwargs):
        seen.append({"cmd": cmd, "stdout": kwargs.get("stdout"), "stderr": kwargs.get("stderr")})
        return SimpleNamespace(pid=777)

    monkeypatch.setattr(base.subprocess, "Popen", record)
    monkeypatch.setattr(process_tracker, "track_process", lambda **kw: None)
    return seen


def test_ssm_tunnel_sin_config_emite_el_comando_de_aws(spawned, aws_cli, monkeypatch):
    """La regresión que importa: el default no se movió."""
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)

    base.BaseCommand().ssm_tunnel(
        instance="i-0123456789", port=9000, local_port=8402,
        region="us-west-2", profile="base-profile",
        remote_host="core.dev.yappycloud.com",
    )

    assert spawned[0]["cmd"] == SSM_ARGV_REMOTE_HOST


def test_ssm_tunnel_con_local_emite_el_forwarder_local(spawned, monkeypatch):
    monkeypatch.setenv("YAPPY_TUNNEL_FORWARDER", "local")

    base.BaseCommand().ssm_tunnel(
        instance="i-0123456789", port=9000, local_port=8402,
        region="us-west-2", profile="base-profile",
        remote_host="core.dev.yappycloud.com",
    )

    assert local_forwarder.MODULE in spawned[0]["cmd"]


def test_ssm_tunnel_avisa_un_puerto_tomado_por_el_log(spawned, aws_cli, tmp_path, monkeypatch):
    """El sink es parte del contrato: un ``DEVNULL`` hacía que un túnel que no
    pudo tomar su puerto se viera igual que uno que todavía estaba levantando,
    y eso es la espera infinita de la que habla ``processes.py:395-398``."""
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)
    log = tmp_path / "tunnel.log"

    base.BaseCommand().ssm_tunnel(
        instance="i-0123456789", port=9000, local_port=8402,
        region="us-west-2", profile="base-profile", quiet=True, log_file=log,
    )

    # El archivo se abre y se cierra adentro del spawn; el Popen ve el handle.
    assert spawned[0]["stdout"] is spawned[0]["stderr"]
    assert spawned[0]["stdout"] is not subprocess.DEVNULL
    assert log.exists()


def test_ssm_tunnel_sigue_trackeando_todo(spawned, aws_cli, monkeypatch):
    """El forwarder se quedó con el argv; el registro es de ``ssm_tunnel`` y no
    se toca. Sin ``local_port`` la fila no puede decir a qué puerto apunta."""
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)
    tracked: list[dict] = []
    monkeypatch.setattr(process_tracker, "track_process", lambda **kw: tracked.append(kw))

    base.BaseCommand().ssm_tunnel(
        instance="i-0123456789", port=9000, local_port=8402,
        region="us-west-2", profile="base-profile",
        remote_host="core.dev.yappycloud.com",
        env="dev", cluster="cap", label="core", owner=process_tracker.OWNER_WEB,
    )

    assert tracked == [{
        "pid": 777,
        "resource": "tunnel",
        "target": "base-profile",
        "env": "dev",
        "log_file": "",
        "local_port": 8402,
        "remote_port": 9000,
        "remote_host": "core.dev.yappycloud.com",
        "cluster": "cap",
        "label": "core",
        "owner": process_tracker.OWNER_WEB,
    }]


def test_ssm_tunnel_abre_el_log_despues_de_ubicar_el_forwarder(
    monkeypatch, tmp_path, aws_cli
):
    """Una máquina sin AWS CLI tiene que fallar **sin haber tocado** el log del
    túnel. Abrirlo primero dejaría un archivo vacío de cero bytes, que es
    exactamente lo que el supervisor lee para explicar un fallo que no pasó.
    """
    monkeypatch.delenv("YAPPY_TUNNEL_FORWARDER", raising=False)
    monkeypatch.setattr(base, "_aws_cmd", _no_aws_cli)
    log = tmp_path / "tunnel.log"

    with pytest.raises(base.ToolUnavailable):
        base.BaseCommand().ssm_tunnel(
            instance="i-1", port=9000, local_port=8402,
            region="us-west-2", profile="p", quiet=True, log_file=log,
        )

    assert not log.exists()


def _no_aws_cli():
    raise base.ToolUnavailable("AWS CLI not found. Install with: pip install awscli")
