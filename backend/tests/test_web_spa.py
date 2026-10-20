"""El catch-all de la SPA no puede servir archivos de fuera del build.

``/{full_path:path}`` matchea cualquier cosa, y un ``..`` dentro de una URL
llega al handler sin normalizar: ``/%2e%2e/secret.txt`` sale de la raíz del
build y ``candidate.is_file()`` lo encuentra igual.

El servidor escucha en ``127.0.0.1``, así que el radio de acción no es "el
developer local": alcanza con que un proceso local, o una página web que el
developer visite, haga un ``GET``. Lo que se alcanzaría es
``backend/config/env.base`` —el profile de AWS— o el ``.env.local``, que tiene
el token de RDS.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from yappy_api import app as app_module


def _client_with_dist(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    """Una app con su propio build, para no depender del `dist` del checkout."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", dist)
    return TestClient(app_module._create_app()), dist


def test_the_spa_serves_the_files_of_the_build(tmp_path, monkeypatch):
    client, dist = _client_with_dist(tmp_path, monkeypatch)
    (dist / "app.js").write_text("console.log('hola')", encoding="utf-8")

    response = client.get("/app.js")

    assert response.status_code == 200
    assert "console.log('hola')" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/..%2Fsecret.txt",
        "/%2e%2e%2fsecret.txt",
        "/%2e%2e/%2e%2e/secret.txt",
    ],
)
def test_the_spa_does_not_serve_a_file_outside_the_build(tmp_path, monkeypatch, path):
    client, _ = _client_with_dist(tmp_path, monkeypatch)
    secret = tmp_path / "secret.txt"
    secret.write_text("AWS_SECRET_VALUE=super-secreto", encoding="utf-8")

    response = client.get(path)

    assert "super-secreto" not in response.text


def test_a_traversal_that_names_a_real_config_file_does_not_leak_it(tmp_path, monkeypatch):
    """El caso que importa: `env.base` tiene el profile de AWS."""
    client, _ = _client_with_dist(tmp_path, monkeypatch)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "env.base").write_text("AWS_PROFILE=base-profile\n", encoding="utf-8")

    response = client.get("/%2e%2e/config/env.base")

    assert "base-profile" not in response.text


def test_an_unknown_path_still_falls_back_to_the_app(tmp_path, monkeypatch):
    """El fallback a `index.html` es lo que hace que el enrutado del Angular funcione."""
    client, _ = _client_with_dist(tmp_path, monkeypatch)

    response = client.get("/aws/params-diff")

    assert response.status_code == 200
    assert "<html>app</html>" in response.text
