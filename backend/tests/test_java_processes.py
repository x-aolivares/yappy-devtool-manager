"""Los procesos Java de la máquina, y cómo se los pregunta.

`jps -l` devuelve una línea por proceso: el pid y el nombre de la clase o del
jar. El parser estaba copiado cinco veces en la app, y cada copia decidía
distinto a qué procesos mata y cómo. Acá está una sola vez, y lo que se hace
con cada proceso es decisión de quien llama.
"""

from __future__ import annotations

import subprocess

import pytest

from yappy_library.adapters import java


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


def test_se_parsean_el_pid_y_el_nombre(monkeypatch):
    monkeypatch.setattr(
        java.subprocess,
        "run",
        lambda *a, **kw: _Result("1234 kafka.Kafka\n5678 main.jar\n"),
    )

    procesos = java.java_processes()

    assert procesos == [("1234", "kafka.Kafka"), ("5678", "main.jar")]


def test_una_linea_incompleta_no_entra(monkeypatch):
    """`jps` puede imprimir líneas que no son un proceso.

    El desinstalador tomaba el primer token de cada línea, así que una línea
    rara lo hacía fallar con un `IndexError`. Acá se ignoran.
    """
    monkeypatch.setattr(
        java.subprocess,
        "run",
        lambda *a, **kw: _Result("1234 kafka.Kafka\nbasura\n\n9012 main.jar\n"),
    )

    procesos = java.java_processes()

    assert procesos == [("1234", "kafka.Kafka"), ("9012", "main.jar")]


def test_sin_jps_no_hay_procesos_pero_no_hay_error(monkeypatch):
    """Una máquina puede no tener Java: eso no es una falla, es una lista vacía."""
    def _no_jps(*args, **kwargs):
        raise FileNotFoundError("jps")

    monkeypatch.setattr(java.subprocess, "run", _no_jps)

    assert java.java_processes() == []


def test_un_jps_que_se_cuelga_no_cuelga_la_app(monkeypatch):
    def _cuelga(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="jps", timeout=10)

    monkeypatch.setattr(java.subprocess, "run", _cuelga)

    assert java.java_processes() == []


def test_matching_filtra_por_nombre(monkeypatch):
    monkeypatch.setattr(
        java.subprocess,
        "run",
        lambda *a, **kw: _Result("1234 kafka.Kafka\n5678 main.jar\n"),
    )

    assert java.matching("kafka.Kafka") == [("1234", "kafka.Kafka")]
    assert java.matching("main.jar") == [("5678", "main.jar")]
    assert java.matching("nada") == []


def test_matar_a_la_fuerza_es_un_taskkill(monkeypatch):
    comandos = []
    monkeypatch.setattr(
        java.subprocess, "run", lambda cmd, **kw: comandos.append(cmd)
    )

    java.kill_java("4242")

    assert comandos == [["taskkill", "/F", "/PID", "4242"]]


def test_se_pregunta_antes_de_matar(monkeypatch):
    """El cierre limpio es lo que mantiene el storage usable.

    Un broker que muere a la fuerza deja el metadata log a medias, y el
    arranque siguiente lo encuentra inválido y muere. Preguntar primero es
    todo el punto.
    """
    asked = []
    killed = []
    monkeypatch.setattr(
        java, "stop_gracefully", lambda pid, timeout=30.0: asked.append(pid) or True
    )
    monkeypatch.setattr(java, "kill_java", lambda pid: killed.append(pid))

    assert java.stop_java("4242") is True
    assert asked == [4242]
    assert killed == []


def test_se_mata_el_que_no_se_va(monkeypatch):
    asked = []
    killed = []
    monkeypatch.setattr(
        java, "stop_gracefully", lambda pid, timeout=30.0: asked.append(pid) or False
    )
    monkeypatch.setattr(java, "kill_java", lambda pid: killed.append(pid))

    assert java.stop_java("4242") is False
    assert asked == [4242]
    assert killed == ["4242"]


def test_un_pid_que_no_es_numero_no_se_mata(monkeypatch):
    killed = []
    monkeypatch.setattr(java, "kill_java", lambda pid: killed.append(pid))

    assert java.stop_java("no-es-un-pid") is False
    assert killed == []


@pytest.mark.parametrize("size", [1, 4, 64])
def test_recv_exactly_devuelve_lo_que_llegó(tmp_path, size):
    from yappy_library.adapters.sockets import recv_exactly

    par = tmp_path / "par"
    par.write_bytes(b"x" * size)

    import socket

    with socket.socket() as a, socket.socket() as b:
        a.bind(("127.0.0.1", 0))
        a.listen(1)
        b.connect(a.getsockname())
        conn, _ = a.accept()
        with conn:
            b.sendall(b"x" * size)
            assert len(recv_exactly(conn, size)) == size
