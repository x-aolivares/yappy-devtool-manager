"""Un broker que se mata a la fuerza deja el storage a medio escribir.

Windows no tiene ``SIGTERM`` para un proceso sin consola: ``taskkill /f`` es
``TerminateProcess``, y una JVM matada así no corre sus shutdown hooks. Para
Kafka eso significa que el metadata log puede quedar con un registro a medio
escribir, y el arranque siguiente encuentra el offset inválido y se muere con un
NPE. De ahí en adelante todos los arranques fallan igual.

Estos tests fijan las dos mitades del arreglo:

- **el broker tiene que poder irse solo**, y para eso necesita una consola
  propia —un ``CTRL_C`` no se puede dirigir a un grupo— y un shim que
  rehabilite el ``Ctrl+C`` en el grupo nuevo;
- **se le pide antes de matarlo**, y el matón queda como último recurso.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

import yappy_library.adapters.kafka.service as kafka_service
import yappy_library.adapters.java as java_adapters
import yappy_library.adapters.processes as processes
from yappy_library.adapters.processes import BaseCommand


# --- la consola que hace posible el apagado limpio ------------------------------


def test_el_broker_tiene_consola_propia_y_oculta(monkeypatch):
    """Consola propia, y sin ventana.

    ``CTRL_C`` no se puede dirigir a un grupo de procesos: va a todos los que
    comparten la consola. Para que el evento llegue sólo al broker, el broker
    tiene que ser el único en su consola.
    """
    monkeypatch.setattr(processes.sys, "platform", "win32")

    kwargs = processes.own_console_popen_kwargs()

    assert kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert not kwargs["creationflags"] & subprocess.DETACHED_PROCESS
    # Y la ventana no aparece en el escritorio del developer.
    assert kwargs["startupinfo"].wShowWindow == subprocess.SW_HIDE


def test_el_shim_devuelve_el_ctrl_c_al_grupo(monkeypatch):
    """Una consola nueva es un grupo nuevo, y en un grupo nuevo el ``Ctrl+C`` está deshabilitado.

    Sin el shim, el broker sería invisible a la señal que lo apaga.
    """
    monkeypatch.setattr(processes.sys, "platform", "win32")

    cmd = processes.jvm_shim_command(["java", "-version"])

    assert cmd[0] == sys.executable
    assert "SetConsoleCtrlHandler(None, False)" in cmd[3]
    assert cmd[-2:] == ["java", "-version"]


def test_en_posix_no_hace_falta_shim(monkeypatch):
    """``SIGTERM`` corre los shutdown hooks: el shim es de Windows."""
    monkeypatch.setattr(processes.sys, "platform", "linux")

    assert processes.jvm_shim_command(["java", "-version"]) == ["java", "-version"]


def test_el_navegador_sigue_detachado(monkeypatch):
    """El navegador no tiene nada que cerrar, así que no cambian sus flags."""
    monkeypatch.setattr(processes.sys, "platform", "win32")

    flags = processes.detached_popen_kwargs()["creationflags"]

    assert flags & subprocess.DETACHED_PROCESS


# --- pedirle que se vaya --------------------------------------------------------


def test_parar_con_gracia_manda_el_ctrl_c(monkeypatch):
    """El evento que se manda es el que corre los hooks, no un matón."""
    sent = []
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_send_ctrl_c", lambda pid: sent.append(pid) or True)
    monkeypatch.setattr(processes.process_identity, "identity", lambda pid: None)

    assert processes.stop_gracefully(4242) is True
    assert sent == [4242]


def test_un_proceso_que_no_se_va_no_es_un_exito(monkeypatch):
    """``False`` no es "no lo paré": es "no hizo caso", y el matón sigue."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_send_ctrl_c", lambda pid: True)
    monkeypatch.setattr(
        processes.process_identity, "identity", lambda pid: SimpleNamespace(exe="java.exe")
    )

    assert processes.stop_gracefully(4242, timeout=0.01) is False


def test_sin_consola_no_se_puede_preguntar(monkeypatch):
    """Un proceso al que no le llega el evento no se puede parar por las buenas."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_send_ctrl_c", lambda pid: False)

    assert processes.stop_gracefully(4242) is False


def test_en_posix_se_manda_el_sigterm(monkeypatch):
    """``SIGTERM`` es la señal de la que el handler corre, y corre los hooks."""
    sent = []
    monkeypatch.setattr(processes.sys, "platform", "linux")
    monkeypatch.setattr(processes.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(processes.process_identity, "identity", lambda pid: None)

    assert processes.stop_gracefully(4242) is True
    assert sent == [(4242, signal.SIGTERM)]


# --- el matón queda de último ---------------------------------------------------


def _java(monkeypatch):
    """Un proceso vivo que es una JVM, y la lista de matones que se hicieron."""
    killed: list[list[str]] = []
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes.process_identity, "parent_map", lambda: {})
    monkeypatch.setattr(
        processes.process_identity,
        "identity",
        lambda pid: SimpleNamespace(exe="java.exe", create_time=100.0),
    )
    monkeypatch.setattr(
        processes.subprocess,
        "run",
        lambda cmd, **kw: killed.append(cmd) or SimpleNamespace(returncode=0),
    )
    return killed


def test_una_jvm_se_pregunta_antes_de_matarla(monkeypatch):
    """El matón de una JVM es lo último, no lo primero."""
    killed = _java(monkeypatch)
    asked = []
    monkeypatch.setattr(
        processes, "stop_gracefully", lambda pid, timeout=30.0: asked.append(pid) or True
    )

    BaseCommand()._kill_pid(4242, expected_exe="java.exe", expected_create_time=100.0)

    assert asked == [4242]
    assert killed == [], "no hizo falta el taskkill: la JVM se fue sola"


def test_un_proceso_que_no_hace_caso_se_mata(monkeypatch):
    """Y si no hace caso, el matón sigue siendo la respuesta."""
    killed = _java(monkeypatch)
    monkeypatch.setattr(
        processes, "stop_gracefully", lambda pid, timeout=30.0: False
    )

    BaseCommand()._kill_pid(4242, expected_exe="java.exe", expected_create_time=100.0)

    assert killed and "taskkill" in killed[0][0]


# --- el servicio de Kafka -------------------------------------------------------


def test_el_broker_se_pregunta_antes_de_matarlo(monkeypatch):
    """``down("server")`` y ``clean()`` salen por el mismo camino."""
    asked = []
    killed = []
    # El cierre elegante vive en `adapters/java.py`, que es donde está el
    # manejo de procesos Java de toda la app.
    monkeypatch.setattr(
        java_adapters, "stop_gracefully", lambda pid, timeout=30.0: asked.append(pid) or True
    )
    monkeypatch.setattr(
        kafka_service.subprocess, "run", lambda cmd, **kw: killed.append(cmd)
    )

    assert kafka_service._stop_java("4242") is True
    assert asked == [4242]
    assert killed == []


def test_los_brokers_que_nosotros_lanzamos_se_preguntan_primero(monkeypatch):
    """El tracker los conoce por el shim, y la consola del shim es la del broker.

    El barrido por nombre queda como estaba —a la fuerza— porque un broker que
    alguien levantó a mano vive en *su* consola, y un ``CTRL_C`` ahí caería en
    medio de lo que estuviera haciendo.
    """
    tracked = [{"pid": 111, "resource": "kafka", "target": "server"}]
    asked = []
    killed = []

    class FakeTracker:
        @staticmethod
        def get_tracked_processes(resource=None, target=None):
            return tracked

    monkeypatch.setattr(kafka_service.process_tracker, "get_tracked_processes",
                        FakeTracker.get_tracked_processes)
    monkeypatch.setattr(
        java_adapters, "stop_gracefully", lambda pid, timeout=30.0: asked.append(pid) or True
    )
    monkeypatch.setattr(
        kafka_service.subprocess,
        "run",
        lambda cmd, **kw: killed.append(cmd) or SimpleNamespace(stdout="", returncode=0),
    )

    kafka_service._kill_kafka_processes()

    assert asked == [111]
