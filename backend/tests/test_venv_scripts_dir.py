"""
El bug que este archivo cubre no es hipotetico: `yappy setup` en Linux nunca
escribia el `.bashrc`.

La causa era una sola linea con el layout de Windows hardcodeado:

    scripts_dir = Path(sys.executable).parent / "Scripts"

En un venv de Unix los ejecutables van en `bin/`, asi que ese directorio nunca
existia, `setup()` caia al `warn()` y no escribia nada. Sin error ni traceback:
la integracion de shell simplemente no se instalaba, y el unico sintoma era que
el comando `yappy` no aparecia en una terminal nueva.

La correccion es que el padre de `sys.executable` ES el directorio buscado: un
venv de Windows guarda `Scripts/python.exe`, uno de Unix `bin/python3`. Agregar
un nivel mas SEARCH `bin/bin`, que es exactamente el error que introdujo el
primer intento del fix.

Este archivo corre en Windows y en Linux, y simula las dos convenciones para que
el fix no pueda romperse del lado contrario.
"""

from __future__ import annotations

import sys
from pathlib import Path

from yappy_library.config import venv_scripts_dir


def _fake_venv(tmp_path: Path, layout: str, monkeypatch) -> Path:
    """Un venv falso con el layout indicado y sys.executable apuntando a el.

    El interprete va DENTRO de la carpeta de scripts, que es la razon por la que
    la funcion mira el padre de sys.executable y no un nivel mas arriba.
    """
    scripts = tmp_path / layout
    scripts.mkdir(parents=True)
    interpreter = scripts / ("python.exe" if sys.platform == "win32" else "python3")
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(interpreter))
    return scripts


def test_resolves_scripts_on_windows_layout(monkeypatch, tmp_path):
    scripts = _fake_venv(tmp_path, "Scripts", monkeypatch)
    assert venv_scripts_dir() == scripts


def test_resolves_bin_on_unix_layout(monkeypatch, tmp_path):
    """La regresion original: en Linux esto no encontraba nada."""
    scripts = _fake_venv(tmp_path, "bin", monkeypatch)
    assert venv_scripts_dir() == scripts


def test_does_not_append_a_second_layout_directory(monkeypatch, tmp_path):
    """Un venv con las dos carpetas: el resultado es la carpeta del interprete,
    nunca `bin/bin`. Ese camino roto fue el primer intento del fix."""
    scripts = _fake_venv(tmp_path, "bin", monkeypatch)
    (tmp_path / "Scripts").mkdir()

    result = venv_scripts_dir()

    assert result == scripts
    assert result.name == "bin"


def test_system_python_returns_the_binary_directory(monkeypatch, tmp_path):
    """Sin venv devuelve el directorio de binarios del sistema, que existe:
    agregar `/usr/bin` al PATH no hace dano."""
    system = tmp_path / "usr" / "bin"
    system.mkdir(parents=True)
    interpreter = system / "python3"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(interpreter))

    assert venv_scripts_dir() == system