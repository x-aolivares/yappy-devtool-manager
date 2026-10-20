"""Quién abre el navegador de Kafdrop, y quién no.

`KafkaService._start_ui(detach=True)` abría el navegador siempre, sin mirar quién
lo pidió. La web usa ese mismo camino —`actions._start_kafka` pasa
`owner=OWNER_WEB`— así que levantar Kafdrop desde la pantalla le robaba el foco
al developer con una ventana que él no pidió, duplicando el link "Abrir ↗" que la
propia fila ya renderiza (`registry.py` le pone `browser_url` a `kafka-ui`).

En la terminal el comportamiento es el correcto y no se toca: desde un comando,
abrir el link gana a hacer que el developer busque un botón, y abrir otra
terminal para clickearlo es peor.

El `os.system` sin detach que usaba este opener ya no está: vive ahora en
`processes.open_url`, y lo comparten los dos que abrían un navegador
(`test_open_url_detached.py`).

El 404 queda fuera de acá a propósito: es otro problema, con otro arreglo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import yappy_library.adapters.kafka.service as api_kafka
from yappy_library.adapters import process_tracker
from yappy_library.adapters.kafka.service import KAFDROP_UI_PORT, KafkaService
from yappy_library.application.workflow.registry import resources_for


def _service(tmp_path) -> KafkaService:
    """Un `KafkaService` con un jar que existe, para no morrer antes de la UI."""
    ui = tmp_path / "kafka-ui"
    ui.mkdir()
    (ui / "main.jar").write_bytes(b"")
    cfg = SimpleNamespace(
        kafka_core_path=str(tmp_path / "kafka-core"),
        kafka_ui_path=str(ui),
        kafka_path=str(tmp_path / "kafka"),
    )
    return KafkaService(cfg)


@pytest.fixture
def navegador(monkeypatch):
    """Sustituye el navegador por una lista, y el spawn por un proceso falso.

    El spawn también se cambia: el punto de estos tests es quién abre el
    navegador, y levantar un `java` de verdad no prueba nada que `_spawn_detached`
    no pruebe en otro lado.
    """
    opened: list[str] = []
    spawned: list[tuple] = []

    def fake_open():
        opened.append("browser")

    def fake_spawn(self, cmd, target, local_port=None, owner="cli"):
        spawned.append((target, local_port, owner))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(api_kafka, "_open_kafdrop_browser", fake_open)
    monkeypatch.setattr(api_kafka.time, "sleep", lambda s: None)
    monkeypatch.setattr(KafkaService, "_spawn_detached", fake_spawn)
    monkeypatch.setattr(api_kafka.sys, "platform", "win32")
    # Que haya un broker corriendo en el puerto de la máquina no tiene que ver
    # con quién abre el navegador.
    monkeypatch.setattr(api_kafka, "_port_in_use", lambda port, **kw: False)
    return opened, spawned


def test_el_arranque_desde_la_web_no_abre_el_navegador(tmp_path, navegador):
    """El caso que llegó: la pantalla levanta Kafdrop y aparece una ventana sola."""
    opened, spawned = navegador

    proc = _service(tmp_path).up("ui", detach=True, owner=process_tracker.OWNER_WEB)

    assert opened == []
    # El proceso se levanta igual: lo que se apagó es el navegador, no Kafdrop.
    assert proc is not None
    assert spawned == [("ui", KAFDROP_UI_PORT, process_tracker.OWNER_WEB)]


def test_desde_la_terminal_sigue_abriendo_el_navegador(tmp_path, navegador):
    """La mitad que no se toca: `yappy kafka up ui` abre Kafdrop solo."""
    opened, _ = navegador

    _service(tmp_path).up("ui", detach=True, owner=process_tracker.OWNER_CLI)

    assert opened == ["browser"]


def test_el_default_sigue_siendo_una_terminal(tmp_path, navegador):
    """Sin `owner` explícito, el que llama es el comando.

    El gate no puede quedar detrás de un default distinto de `OWNER_CLI`: la
    terminal es el caso viejo y es el que no debe cambiar.
    """
    opened, _ = navegador

    _service(tmp_path).up("ui", detach=True)

    assert opened == ["browser"]


def test_el_broker_no_abre_nada(tmp_path, navegador):
    """`_start_server` nunca abrió un navegador, y ahora tampoco.

    Fijado porque el gate vive en `_start_ui` y no en un lugar compartido: si
    alguien lo sube de nivel, el broker es el que paga.
    """
    opened, _ = navegador
    core = tmp_path / "kafka-core"
    (core / "config" / "kraft").mkdir(parents=True)
    (core / "libs").mkdir()
    (core / "config" / "kraft" / "server.properties").write_text(
        "listeners=PLAINTEXT://:9092,CONTROLLER://:9093\n", encoding="utf-8"
    )
    svc = _service(tmp_path)
    svc._kafka_core = core

    svc.up("server", detach=True, owner=process_tracker.OWNER_WEB)

    assert opened == []


def test_la_fila_de_kafdrop_conserva_el_link(tmp_path):
    """El motivo de no abrir el navegador desde la web es que el link está.

    Si algún cleanup saca `browser_url` de `kafka-ui`, el gate de arriba pasa a
    ser la diferencia entre "no abre nada" y "abre algo", y este test es lo que
    avisa de que se rompió la mitad buena.
    """
    cfg = SimpleNamespace(
        env="dev",
        db_port=8100,
        kafka_core_path=str(tmp_path / "no-existe"),
        get=lambda key, default=None: default,
    )

    ui = next(r for r in resources_for(cfg) if r.key == "kafka-ui")

    assert ui.browser_url == f"http://localhost:{KAFDROP_UI_PORT}"