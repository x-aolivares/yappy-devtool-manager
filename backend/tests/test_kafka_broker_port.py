"""El puerto del broker, leído de su propia configuración.

El caso que motivó esto: `listeners=PLAINTEXT://:9092,CONTROLLER://:9093`. Tomar
el último `:` de la línea —que es lo que hacía la primera versión— devuelve 9093, el
puerto del controller, donde nadie habla protocolo Kafka. La pantalla decía que el
broker estaba en 9093 y el readiness probe le hablaba al puerto equivocado, así que
un broker sano se reportaba caído y uno roto se reportaba por la razón equivocada.
"""

from __future__ import annotations

import pytest

from yappy_library.adapters.kafka.service import DEFAULT_BROKER_PORT, _broker_port


def _props(tmp_path, listeners: str):
    path = tmp_path / "server.properties"
    path.write_text(
        f"process.roles=broker,controller\nnode.id=1\nlisteners={listeners}\n",
        encoding="utf-8",
    )
    return path


def test_el_puerto_del_broker_no_es_el_del_controller(tmp_path):
    """El caso real de la máquina donde se encontró el bug."""
    props = _props(tmp_path, "PLAINTEXT://:9092,CONTROLLER://:9093")

    assert _broker_port(props) == 9092


def test_un_solo_listener_sin_controller(tmp_path):
    props = _props(tmp_path, "PLAINTEXT://:9092")

    assert _broker_port(props) == 9092


def test_un_listener_con_nombre(tmp_path):
    props = _props(tmp_path, "EXTERNAL://0.0.0.0:19092")

    assert _broker_port(props) == 19092


def test_el_controller_puede_ir_primero(tmp_path):
    """El orden en el archivo no dice cuál es cuál."""
    props = _props(tmp_path, "CONTROLLER://:9093,PLAINTEXT://:9092")

    assert _broker_port(props) == 9092


def test_un_archivo_ilegible_cae_al_default(tmp_path):
    """El caller anota una pista para una pantalla, no configura el broker: un
    archivo roto no puede ser fatal."""
    props = tmp_path / "no-existe.properties"

    assert _broker_port(props) == DEFAULT_BROKER_PORT


def test_una_linea_sin_puerto_utilizable_cae_al_default(tmp_path):
    props = _props(tmp_path, "PLAINTEXT://:noesunpuerto")

    assert _broker_port(props) == DEFAULT_BROKER_PORT