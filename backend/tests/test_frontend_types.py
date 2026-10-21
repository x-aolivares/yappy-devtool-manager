"""La config de tipos del frontend tiene que seguir estricta.

El `tsconfig.json` del frontend no tenía `strict` ni `strictTemplates`, así
que los templates de Angular no se chequeaban y `undefined` pasaba por el
código sin que nadie lo viera. Con `strict` apagado, un `this.state()` que
puede no existir se usa como si existiera.

El build de Angular **es** el type check: `ng build` falla si un template no
cumple el tipo que el componente dice tener. Por eso esto no necesita un paso
extra en el CI — pero sí necesita que alguien no lo apague sin darse cuenta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TSCONFIG = ROOT / "frontend" / "tsconfig.json"
TSCONFIG_SPEC = ROOT / "frontend" / "tsconfig.spec.json"

#: Los `tsconfig.json` son JSON **con comentarios**, y `json` no los entiende.
#: El patrón sólo matchea un `//` que empieza la línea o que viene después de
#: un espacio, así que un `"http://..."` dentro de un valor no se toca.
_COMENTARIO_LINEA = re.compile(r"(^|\s)//.*$", re.MULTILINE)
_COMENTARIO_BLOQUE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _leer_jsonc(path: Path) -> dict:
    texto = path.read_text(encoding="utf-8")
    texto = _COMENTARIO_BLOQUE.sub("", texto)
    texto = _COMENTARIO_LINEA.sub("", texto)
    return json.loads(texto)


def _config() -> dict:
    return _leer_jsonc(TSCONFIG)


def test_strict_esta_prendido():
    """Sin `strict`, `strictNullChecks` está apagado y `undefined` fluye."""
    compiler = _config()["compilerOptions"]
    assert compiler.get("strict") is True


def test_los_templates_de_angular_se_chequean():
    """`strictTemplates` es lo que hace que un `@if` con un tipowrong falle.

    Sin esto, el template se compila sin mirar los tipos del componente: es
    la mitad de la app, y la mitad que más se rompe.
    """
    angular = _config()["angularCompilerOptions"]
    assert angular.get("strictTemplates") is True


def test_el_acceso_a_diccionario_es_honesto():
    """`noUncheckedIndexedAccess` dice que un índice puede no estar.

    Sin eso, `diccionario['clave']` es del tipo que dice que existe, y un
    `??` de respaldo se ve como código muerto.
    """
    compiler = _config()["compilerOptions"]
    assert compiler.get("noUncheckedIndexedAccess") is True


def test_los_specs_no_estan_en_el_alcance_estricto():
    """Los specs son código de test, y se chequean con la config de siempre.

    Es el mismo criterio que el gate de mypy: el gate estricto es para el
    código que se despliega, y `tsconfig.app.json` —que es el que compila la
    app— excluye los specs.
    """
    spec = _leer_jsonc(TSCONFIG_SPEC)
    assert spec["compilerOptions"].get("noUncheckedIndexedAccess") is False
