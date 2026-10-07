"""El proxy de `ng serve` tiene que matchear los endpoints anidados de la API.

En modo `yappy web --watch` el navegador pega a `/api/...` contra el dev server y
depende del proxy para que llegue al backend. Si el glob no matchea, el dev server
responde con el index.html de la SPA y la API nunca se consulta: el síntoma es
"las consultas no llegan al backend" aunque el backend esté impecable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from yappy_cli.cli import _write_temp_proxy, get_project_root

# Endpoints reales de la API. Los anidados son los que-rompen con "/api/*":
# un `*` de glob no cruza la "/" y Angular traduce el patrón con picomatch.
API_PATHS = [
    "/api/envs",
    "/api/query",
    "/api/params/read",
    "/api/params/diff",
    "/api/db/schemas",
    "/api/execute/sql",
    "/api/sessions/abc/items",
    "/api/sessions/abc/report.md",
]

# Rutas que el dev server tiene que servirse él, nunca proxear.
NOT_API_PATHS = ["/", "/main.js", "/api", "/apiary"]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """El mismo glob -> regex que hace picomatch para `ng serve`.

    Sólo alcanza con la regla que differentiatesó este bug: `**` cruza la "/",
    `*` no. Si aparece un carácter de glob raro, el test falla en vez de
    inventar una traducción que no sea la real.
    """
    assert set(pattern) <= set("/api*?[]"), f"glob con sintaxis no soportada: {pattern}"
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char in "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _assert_covers_api(config: dict) -> None:
    (pattern,) = list(config)
    regex = _glob_to_regex(pattern)
    for path in API_PATHS:
        assert regex.match(path), f"{pattern} no matchea {path}"
    for path in NOT_API_PATHS:
        assert not regex.match(path), f"{pattern} matchea {path} y no debería"


def test_repo_proxy_config_matches_nested_api_paths():
    config = json.loads((get_project_root() / "frontend" / "proxy.conf.json").read_text("utf-8"))
    _assert_covers_api(config)


def test_temp_proxy_matches_nested_api_paths(tmp_path: Path):
    _assert_covers_api(json.loads(_write_temp_proxy(tmp_path, "http://127.0.0.1:8765").read_text("utf-8")))


def test_temp_proxy_points_at_the_given_port(tmp_path: Path):
    config = json.loads(_write_temp_proxy(tmp_path, "http://127.0.0.1:9911").read_text("utf-8"))
    (entry,) = config.values()
    assert entry["target"] == "http://127.0.0.1:9911"