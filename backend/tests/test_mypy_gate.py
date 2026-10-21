"""Mypy gate: las anotaciones tienen que ser verdad.

Este repo está anotado a mano —`from __future__ import annotations`, `str |
None`, `dict[str, str]`, `-> tuple[str, int]`— lo que significa que la
intención ya está escrita en el código. Lo que faltaba era el que la mirara, y
eso es lo que este gate hace.

**No es un gate de estilo.** No pide anotaciones donde no las hay: los
`disallow_untyped_defs` y compañía están apagados a propósito, porque el repo
tiene funciones sin anotar y anotarlas todas es un trabajo que no aporta
nada. Lo que se mira es que los tipos que el código **dice** tener sean los que
tiene.

**No mira los tests.** Usan dobles sintéticos donde la producción usa objetos
reales, y `monkeypatch` de un método no es un error. La mitad de los errores
que mypy reportaba en los tests era el patrón `lambda: calls.append(x)`, que
es correcto en Python.

**El alcance de `files` es el código de producción**, y el test lo fija: si
alguien agrega `backend/tests` al alcance, este test falla.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Lo que mypy tiene que mirar. Es el código que corre en producción: la
#: library, la API y el CLI.
TARGETS = ["backend/library", "backend/api", "backend/cli"]


def _mypy_available() -> bool:
    return (
        shutil.which("mypy") is not None
        or subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            capture_output=True,
        ).returncode
        == 0
    )


requires_mypy = pytest.mark.skipif(
    not _mypy_available(), reason="mypy not installed (see docs/requirements-dev.txt)"
)


def test_mypy_is_declared_in_dev_requirements():
    """The gate must be reproducible on a clean environment."""
    dev = (ROOT / "docs" / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "mypy" in dev, "mypy must be declared in docs/requirements-dev.txt"


def test_mypy_only_looks_at_the_production_code():
    """Los tests no entran al gate.

    Usan dobles sintéticos donde la producción usa objetos reales, y
    `monkeypatch` de un método no es un error. Si algún día el alcance se
    amplia, este test avisa.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'files = ["backend/library", "backend/api", "backend/cli"]' in text


def test_mypy_checks_the_bodies_of_untyped_functions():
    """Sin esto, mypy no mira la mitad del código.

    `check_untyped_defs` es la diferencia entre un gate que existe y uno que
    decora. Sin él, las funciones sin anotar —que son la mayoría— no se
    chequean, y el gate pasa por código que nunca miró.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "check_untyped_defs = true" in text


@requires_mypy
def test_mypy_passes_on_the_production_code():
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "mypy found type errors:\n" f"{result.stdout}\n{result.stderr}"
    )
