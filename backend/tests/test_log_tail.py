"""La cola de un log se lee con tope, no entera.

``last_lines`` leía el archivo completo con ``read_bytes()``. El camino
incremental ya estaba capado (``MAX_CHUNK_BYTES``), así que la bomba era sólo
la primera lectura: un log de Kafka de 2 GB significaba 2 GB en memoria por
conexión de stream abierta.
"""

from __future__ import annotations

from yappy_library.adapters.log_tail import MAX_CHUNK_BYTES, last_lines, read_from


def test_la_cola_devuelve_las_ultimas_lineas(tmp_path):
    log = tmp_path / "kafka.log"
    log.write_text("\n".join(f"linea {i}" for i in range(100)), encoding="utf-8")

    tail = last_lines(log, lines=10)

    assert tail.splitlines()[-1] == "linea 99"
    assert len(tail.splitlines()) == 10


def test_la_primera_lectura_no_lee_el_archivo_entero(tmp_path):
    """El tope es lo que evita que un log enorme se cargue en memoria."""
    log = tmp_path / "kafka.log"
    with log.open("wb") as handle:
        handle.write(b"x" * (MAX_CHUNK_BYTES * 4))
        handle.write(b"\nlinea final\n")

    tail = last_lines(log, lines=5)

    # Lo que hay antes del tope no se lee: el chunk arranca en el último
    # `MAX_CHUNK_BYTES` del archivo.
    assert len(tail.encode("utf-8")) <= MAX_CHUNK_BYTES
    assert "linea final" in tail


def test_la_primera_linea_parcial_se_descarta(tmp_path):
    """El chunk arranca en medio de una línea: esa línea está cortada.

    Mostrarla tal cual confunde más de lo que ayuda —parece una línea
    entera que no existe— así que se tira. Las líneas siguientes no se
    pierden: sólo se pierde la primera, que no está completa.
    """
    log = tmp_path / "kafka.log"
    with log.open("wb") as handle:
        handle.write(b"primera\n")
        handle.write(b"una linea larga que se corta\n")
        handle.write(b"tercera\n")
        handle.write(b"cuarta\n")

    tail = last_lines(log, lines=10, max_bytes=30)

    assert "una linea larga" not in tail
    assert "tercera" in tail
    assert "cuarta" in tail


def test_un_archivo_inexistente_no_revienta(tmp_path):
    assert last_lines(tmp_path / "nada.log", lines=10) == ""


def test_el_cursor_sigue_andando_despues_de_la_cola(tmp_path):
    """La cola es la primera lectura; después, sólo lo nuevo."""
    log = tmp_path / "kafka.log"
    log.write_text("primera\n", encoding="utf-8")

    tail = last_lines(log, lines=10)
    offset = log.stat().st_size

    with log.open("ab") as handle:
        handle.write(b"segunda\n")

    chunk, new_offset = read_from(log, offset)

    assert chunk == "segunda\n"
    assert new_offset > offset
    assert "primera" in tail
