"""El catálogo de repositorios se consulta una vez, y sólo cuando toca.

Tres reglas, que son las que este archivo fija:

1. **La caché fresca no paga el viaje.** Abrir la pantalla no debería hacer
   20 requests a Bitbucket para llenar un input.
2. **El refresh sí la paga.** Sin eso, un repo creado hoy no aparece hasta
   mañana y no hay forma de forzarlo.
3. **Una consulta fallida no deja el input sin sugerencias** si hay lista vieja:
   una lista un poco desactualizada es mejor que ninguna.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from yappy_api import repo_catalog
from yappy_library.application.deployment import repos as repos_mod
from yappy_library.application.deployment.ecs_branch import DeploymentUnresolved


@pytest.fixture(autouse=True)
def catalogo_en_temp(monkeypatch, tmp_path):
    """La base del catálogo va a un temp: ningún test toca la de la máquina."""
    monkeypatch.setenv("YAPPY_REPO_CATALOG_DB", str(tmp_path / "repo_catalog.db"))


def _client():
    from fastapi.testclient import TestClient

    from yappy_api.app import app

    return TestClient(app)


def _fake_cfg(**values):
    class _Cfg:
        def get(self, key, default=None):
            return values.get(key, default)

    return _Cfg()


class _FakeBitbucket:
    """El listado de Bitbucket, configurable por test y sin red."""

    def __init__(self, names=None, error=None):
        self.names = names if names is not None else []
        self.error = error
        self.calls = 0

    def __call__(self, cfg=None):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.names)


def _instalo(monkeypatch, bitbucket):
    monkeypatch.setattr(repos_mod, "fetch_repositories", bitbucket)
    return bitbucket


# --- el TTL y el refresh ------------------------------------------------------


def test_sin_cache_consulta_bitbucket_y_guarda(monkeypatch):
    # El orden lo garantiza `fetch_repositories` (test de abajo); acá lo que se
    # comprueba es que lo que se pidió es lo que queda guardado.
    bitbucket = _instalo(monkeypatch, _FakeBitbucket(["yappy-a", "yappy-b"]))

    response = _client().get("/api/deployments/repos")

    assert response.status_code == 200
    body = response.json()
    assert body["repositories"] == ["yappy-a", "yappy-b"]
    assert body["stale"] is False
    assert body["fetched_at"] is not None
    assert bitbucket.calls == 1
    # Lo que quedó en la base es lo que se devolvió: si se pierde el frontend,
    # la próxima consulta tiene desde dónde arrancar.
    assert repo_catalog.read(body["workspace"])["repositories"] == ["yappy-a", "yappy-b"]


def test_con_cache_fresca_no_vuelve_a_consultar(monkeypatch):
    bitbucket = _instalo(monkeypatch, _FakeBitbucket(["yappy-a"]))
    client = _client()

    client.get("/api/deployments/repos")
    body = client.get("/api/deployments/repos").json()

    assert bitbucket.calls == 1, "la caché de menos de 24 h pagó el viaje igual"
    assert body["repositories"] == ["yappy-a"]


def test_el_refresh_fuerza_la_consulta(monkeypatch):
    """Es el botón "Actualizar": es lo único que muestra un repo de hoy."""
    bitbucket = _instalo(monkeypatch, _FakeBitbucket(["yappy-a"]))
    client = _client()
    client.get("/api/deployments/repos")

    body = client.get("/api/deployments/repos", params={"refresh": "true"}).json()

    assert bitbucket.calls == 2
    assert body["stale"] is False


def test_un_cache_vencido_se_vuelve_a_consultar(monkeypatch):
    bitbucket = _instalo(monkeypatch, _FakeBitbucket(["yappy-nuevo"]))
    _client().get("/api/deployments/repos")
    _envejece(25)

    body = _client().get("/api/deployments/repos").json()

    assert bitbucket.calls == 2
    assert body["repositories"] == ["yappy-nuevo"]


def _envejece(hours: int, workspace: str = "bg-ti") -> None:
    """Deja el `fetched_at` de la caché `hours` horas en el pasado."""
    old = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    path = repo_catalog._db_path()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE repo_catalog SET fetched_at = ? WHERE workspace = ?",
            (old, workspace),
        )


# --- cuando Bitbucket falla ---------------------------------------------------


def test_si_bitbucket_falla_se_sirve_la_cache_vieja(monkeypatch):
    """Un input sin sugerencias es peor que una lista un poco vieja."""
    _instalo(monkeypatch, _FakeBitbucket(["yappy-a"]))
    _client().get("/api/deployments/repos")
    _instalo(
        monkeypatch,
        _FakeBitbucket(error=DeploymentUnresolved("network_error", "No se pudo llegar a Bitbucket")),
    )

    response = _client().get("/api/deployments/repos", params={"refresh": "true"})

    assert response.status_code == 200
    body = response.json()
    assert body["repositories"] == ["yappy-a"]
    assert body["stale"] is True


def test_si_bitbucket_falla_sin_cache_es_un_502(monkeypatch):
    """Sin nada que servir, el fallo se avisa: el 502 es lo que ya hace la cadena."""
    _instalo(
        monkeypatch,
        _FakeBitbucket(error=DeploymentUnresolved("bitbucket_unavailable", "Falta BITBUCKET_TOKEN")),
    )

    response = _client().get("/api/deployments/repos")

    assert response.status_code == 502
    assert "BITBUCKET_TOKEN" in response.json()["detail"]


# --- el TTL -------------------------------------------------------------------


def test_la_cache_de_menos_de_24_h_esta_fresca():
    assert repo_catalog.is_fresh(datetime.now().isoformat(timespec="seconds"))


def test_la_cache_de_mas_de_24_h_esta_vencida():
    vencida = (datetime.now() - timedelta(hours=24, minutes=1)).isoformat(timespec="seconds")

    assert not repo_catalog.is_fresh(vencida)


def test_una_fecha_ilegible_cuenta_como_vencida():
    """Es más seguro pagar el viaje que servir una fecha que no se entiende."""
    assert not repo_catalog.is_fresh("ayer a las tres")
    assert not repo_catalog.is_fresh("")


# --- el listado en sí ---------------------------------------------------------


def _pagina(monkeypatch, paginas: list[dict]):
    """Devuelve `(nombres, urls)` de `fetch_repositories` respondiendo página por página."""
    urls = []

    def _get(url, headers):
        urls.append(url)
        return json.dumps(paginas[min(len(urls) - 1, len(paginas) - 1)])

    monkeypatch.setattr(repos_mod, "_http_get", _get)
    names = repos_mod.fetch_repositories(_fake_cfg(BITBUCKET_TOKEN="tok", BITBUCKET_WORKSPACE="bg-ti"))
    return names, urls


def test_el_listado_se_ordena_sin_repetir(monkeypatch):
    paginas = [
        {
            "values": [{"name": "yappy-b"}, {"name": "yappy-a"}, {"name": "yappy-b"}],
            "next": "https://api.bitbucket.org/2.0/repositories/bg-ti?page=2",
        },
        {"values": [{"name": "yappy-c"}]},
    ]

    names, urls = _pagina(monkeypatch, paginas)

    assert names == ["yappy-a", "yappy-b", "yappy-c"]
    assert len(urls) == 2


def test_el_listado_sigue_las_paginas(monkeypatch):
    paginas = [
        {
            "values": [{"name": "a"}],
            "next": "https://api.bitbucket.org/2.0/repositories/bg-ti?page=2",
        },
        {"values": [{"name": "b"}]},
    ]

    names, urls = _pagina(monkeypatch, paginas)

    assert names == ["a", "b"]
    assert "page=2" in urls[1]


def test_un_next_que_no_avanca_no_es_un_loop_infinito(monkeypatch):
    """El tope de páginas existe por esto: un `next` siempre igual nunca corta."""
    misma = "https://api.bitbucket.org/2.0/repositories/bg-ti?page=2"
    paginas = [{"values": [{"name": "a"}], "next": misma}]

    _, urls = _pagina(monkeypatch, paginas)

    assert len(urls) == repos_mod._MAX_REPO_PAGES


def test_sin_token_no_se_consulta_nada(monkeypatch):
    """Es el mismo corte que la cadena de deploy: el mensaje dice qué falta."""
    monkeypatch.setattr(
        repos_mod,
        "_http_get",
        lambda *a: pytest.fail("no debería pedirle nada a Bitbucket sin token"),
    )

    with pytest.raises(DeploymentUnresolved) as excinfo:
        repos_mod.fetch_repositories(_fake_cfg(BITBUCKET_WORKSPACE="bg-ti"))

    assert excinfo.value.status == "bitbucket_unavailable"
    assert "BITBUCKET_TOKEN" in excinfo.value.message


def test_una_respuesta_que_no_es_json_es_un_error_declarado(monkeypatch):
    monkeypatch.setattr(repos_mod, "_http_get", lambda url, headers: "<html>rate limited</html>")

    with pytest.raises(DeploymentUnresolved) as excinfo:
        repos_mod.fetch_repositories(_fake_cfg(BITBUCKET_TOKEN="tok"))

    assert excinfo.value.status == "bad_response"


def test_el_workspace_sale_de_la_config(monkeypatch):
    """El mismo workspace que usa la cadena de deploy, no uno hardcodeado."""
    urls = []
    monkeypatch.setattr(
        repos_mod, "_http_get", lambda url, headers: urls.append(url) or json.dumps({"values": []})
    )

    repos_mod.fetch_repositories(_fake_cfg(BITBUCKET_TOKEN="tok", BITBUCKET_WORKSPACE="otro-ws"))

    assert "/repositories/otro-ws" in urls[0]
