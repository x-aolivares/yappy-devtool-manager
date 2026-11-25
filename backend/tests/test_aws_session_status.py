"""El estado que consume el sidebar viene del cache SSO del backend.

El front ya no compara `expires_at` con su propio reloj: descuenta
`expires_in_seconds` desde que recibió la respuesta. Estos tests fijan que ese
número sea siempre el que manda el server —positivo mientras la sesión está
viva, 0 cuando venció, ausente cuando no se puede saber—, que es justo lo que
evita que un reloj corrido muestre «Activa» con la sesión caducada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from yappy_api.aws_auth import cache_status


def _cache(tmp_path, *, expires_at: str | None, token: str | None = "token"):
    path = tmp_path / "aws-sso-cache.json"
    data: dict[str, object] = {"startUrl": "https://example.awsapps.com/start"}
    if token is not None:
        data["accessToken"] = token
    if expires_at is not None:
        data["expiresAt"] = expires_at
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _status(path):
    return cache_status(path, profile="base-profile", sso_session="base", start_url="https://example.awsapps.com/start")


def test_token_vivo_devuelve_segundos_restantes(tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(minutes=12)
    status = _status(_cache(tmp_path, expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ")))

    assert status["active"] is True
    assert status["message"] is None
    assert status["expires_in_seconds"] is not None
    # `int()` truncó como máximo un segundo; el front descuenta desde que
    # recibe la respuesta, así que una tolerancia de 1s es suficiente.
    assert 719 <= status["expires_in_seconds"] <= 720


def test_token_vencido_informa_cero_y_no_activo(tmp_path):
    expires = datetime.now(timezone.utc) - timedelta(minutes=5)
    status = _status(_cache(tmp_path, expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ")))

    assert status["active"] is False
    assert status["expires_in_seconds"] == 0
    assert "venció" in status["message"]


def test_sin_cache_no_hay_segundos_y_el_mensaje_explica_por_que(tmp_path):
    status = _status(tmp_path / "no-existe.json")

    assert status["active"] is False
    assert status["expires_in_seconds"] is None
    assert status["message"] == "No hay una sesión SSO cacheada."


def test_expires_at_ilegible_no_rompe_el_estado(tmp_path):
    status = _status(_cache(tmp_path, expires_at="no-es-una-fecha"))

    assert status["active"] is False
    assert status["expires_in_seconds"] is None
    assert status["message"] == "La sesión de AWS venció o no está activa."


def test_sin_access_token_no_es_activa_aunque_haya_expiracion(tmp_path):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    status = _status(_cache(tmp_path, expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"), token=None))

    assert status["expires_in_seconds"] is not None
    assert status["expires_in_seconds"] > 0
    assert status["active"] is False
