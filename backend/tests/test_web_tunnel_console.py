"""Un túnel levantado desde la web no puede compartir la consola del server.

El síntoma que fijan estos tests no se ve en el request que lo provoca: el
``POST /api/workflow/stop`` contesta 200 y un instante después uvicorn escribe
``Shutting down``. En Windows un ``CTRL_C_EVENT`` no se puede dirigir a un pid:
se manda a **todos** los procesos enganchados a una consola. Los túneles se
lanzaban con un ``Popen`` sin flags, heredaban la de uvicorn, y parar uno
cerraba también la API que estaba atendiendo el click.

La CLI es el caso contrario y también está acá: en una terminal el ``Ctrl+C``
sí tiene que llegar al túnel, así que ese camino no cambia.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import yappy_library.adapters.processes as processes
import yappy_library.application.workflow.actions as actions
from yappy_library.adapters import process_tracker


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        env="qa",
        profile="base-profile",
        require=lambda key: {
            "AWS_INSTANCE": "i-123",
            "AWS_REGION": "us-west-1",
        }[key],
    )


def _record_popen(monkeypatch) -> dict:
    seen: dict = {}
    monkeypatch.setattr(
        processes.subprocess,
        "Popen",
        lambda cmd, **kw: seen.update(kw) or SimpleNamespace(pid=4242),
    )
    return seen


def test_el_tunnel_de_la_web_pide_detach(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        processes.BaseCommand,
        "ssm_tunnel",
        lambda self, **kw: seen.update(kw) or SimpleNamespace(pid=4242),
    )

    actions._spawn_tunnel(
        _cfg(), port=8403, local_port=8403, remote_host="host.qa",
        cluster="cap", label="Commerce", env="qa",
    )

    assert seen["detached"] is True
    assert seen["owner"] == process_tracker.OWNER_WEB


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_el_detach_llega_hasta_el_popen(monkeypatch, platform):
    """El permiso viaja de `_spawn_tunnel` a `Popen`, no se queda en el action."""
    seen = _record_popen(monkeypatch)
    monkeypatch.setattr(processes.sys, "platform", platform)
    monkeypatch.setattr(processes, "_aws_cmd", lambda: ["aws"])
    monkeypatch.setattr(process_tracker, "track_process", lambda **kw: None)

    actions._spawn_tunnel(
        _cfg(), port=8403, local_port=8403, remote_host=None,
        cluster="cap", label="Commerce", env="qa",
    )

    for key, value in processes.detached_popen_kwargs().items():
        assert seen[key] == value


def test_la_cli_no_pide_detach(monkeypatch):
    """Desde un terminal el Ctrl+C del developer sí debe cerrar el túnel."""
    seen: dict = {}
    monkeypatch.setattr(
        processes.BaseCommand,
        "popen",
        lambda self, cmd, **kw: seen.update(kw) or SimpleNamespace(pid=4242),
    )
    monkeypatch.setattr(processes, "_aws_cmd", lambda: ["aws"])
    monkeypatch.setattr(process_tracker, "track_process", lambda **kw: None)

    processes.BaseCommand().ssm_tunnel(
        instance="i-123", port=1, local_port=2, region="us-west-1", profile="base",
    )

    assert seen["detached"] is False


def test_un_popen_sin_detach_no_hereda_flags(monkeypatch):
    """Ningún otro llamador de `popen` ve cambiado su comportamiento."""
    seen = _record_popen(monkeypatch)

    processes.BaseCommand().popen(["aws", "ssm"])

    assert "creationflags" not in seen
    assert "start_new_session" not in seen
    assert seen["stdout"] is None and seen["stderr"] is None


def test_los_flags_son_los_mismos_que_usa_el_navegador(monkeypatch):
    """Una sola definición de "fuera de nuestra consola", compartida con open_url."""
    monkeypatch.setattr(processes.sys, "platform", "win32")

    flags = processes.detached_popen_kwargs()["creationflags"]

    assert flags & getattr(subprocess, "DETACHED_PROCESS", 0x8)
    assert flags & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)


# --- el freno para entradas viejas del tracker ---------------------------------


def _record_ctrl_c_sender(monkeypatch) -> list[list[str]]:
    sent: list[list[str]] = []
    monkeypatch.setattr(
        processes.subprocess,
        "run",
        lambda cmd, **kw: sent.append(cmd) or SimpleNamespace(returncode=0),
    )
    return sent


def test_no_se_manda_ctrl_c_a_un_proceso_de_nuestra_consola(monkeypatch):
    """Un túnel viejo, iniciado antes del fix, ya no se lleva al server por delante."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_console_members", lambda: {4242, 99})
    sent = _record_ctrl_c_sender(monkeypatch)

    assert processes._send_ctrl_c(4242) is False
    assert sent == []


def test_si_no_se_puede_leer_la_consola_no_se_arriesga_el_ctrl_c(monkeypatch):
    """Ante la duda, el matón con guard de ancestry es menos peligroso."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_console_members", lambda: None)
    sent = _record_ctrl_c_sender(monkeypatch)

    assert processes._send_ctrl_c(4242) is False
    assert sent == []


def test_un_proceso_ajeno_a_nuestra_consola_sí_recibe_el_ctrl_c(monkeypatch):
    """El apagado elegante de Kafka sigue funcionando: tiene consola propia."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_console_members", lambda: {99})
    sent = _record_ctrl_c_sender(monkeypatch)

    assert processes._send_ctrl_c(4242) is True
    assert sent and sent[0][-1] == "4242"


def test_en_posix_no_hay_consola_que_proteger(monkeypatch):
    monkeypatch.setattr(processes.sys, "platform", "linux")
    monkeypatch.setattr(
        processes,
        "_console_members",
        lambda: pytest.fail("no se debe consultar la consola fuera de Windows"),
    )
    sent = _record_ctrl_c_sender(monkeypatch)

    assert processes._send_ctrl_c(4242) is True
    assert sent and sent[0][-1] == "4242"


def test_sin_windll_no_se_manda_el_evento(monkeypatch):
    """Un host sin kernel32 no puede confirmar la consola: no dispara a ciegas."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    sent = _record_ctrl_c_sender(monkeypatch)

    def explode():
        raise AttributeError("windll")

    monkeypatch.setattr(processes, "_console_members", explode)

    # La protección vive en `_send_ctrl_c`, no en el helper: aunque el helper
    # cambie, un error de lectura nunca debe convertirse en un CTRL_C a ciegas.
    assert processes._send_ctrl_c(4242) is False
    assert sent == []


def test_stop_gracefully_tampoco_intenta_el_ctrl_c_compartido(monkeypatch):
    """El guard se aplica en la ruta real de parada, no sólo en el helper."""
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setattr(processes, "_console_members", lambda: {4242})
    sent = _record_ctrl_c_sender(monkeypatch)

    assert processes.stop_gracefully(4242, timeout=0.01) is False
    assert sent == []
