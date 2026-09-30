"""Tests de la config de proxy del frontend.

El proxy existe para que el dev server reenviese /api -> API sin pasar por CORS.
Lo que importa es que: (a) el prefijo se saque, (b) el puerto venga del enum y
no de un numero escrito a mano, y (c) no se pisen configuraciones entre llamadas.
"""
import json

from web.api.ports_registry import YappyPort
from web.api.proxy_config import GENERATED_NAME, path, write


def test_generates_a_file_named_for_ng_serve(tmp_path):
    target = write(tmp_path, int(YappyPort.WEB_API))

    assert target.name == GENERATED_NAME
    assert target.exists()


def test_path_rewrites_the_api_prefix_away(tmp_path):
    """El backend monta /environments, no /api/environments. Sin el rewrite el
    proxy devuelve 404 y parece un bug de la UI."""
    config = json.loads(write(tmp_path, 8300).read_text())

    assert config["/api"]["pathRewrite"] == {"^/api": ""}


def test_target_uses_the_port_from_the_enum(tmp_path):
    config = json.loads(write(tmp_path, 8300).read_text())

    assert config["/api"]["target"] == f"http://127.0.0.1:{YappyPort.WEB_API}"


def test_port_comes_from_the_argument_not_a_hardcoded_literal(tmp_path):
    """Si YappyPort.WEB_API cambia, el proxy lo sigue sin tocar este archivo."""
    config = json.loads(write(tmp_path, 9999).read_text())

    assert config["/api"]["target"] == "http://127.0.0.1:9999"


def test_writes_valid_json(tmp_path):
    target = write(tmp_path, 8300)

    parsed = json.loads(target.read_text())
    assert isinstance(parsed, dict)
    assert "/api" in parsed


def test_regenerating_does_not_accumulate_keys(tmp_path):
    """Un deep copy por llamada: si se reusara el dict de módulo y se le
    cambiara el target, las escrituras anteriores se contaminarían."""
    write(tmp_path, 8300)
    config = json.loads(write(tmp_path, 8600).read_text())

    assert config["/api"]["target"] == "http://127.0.0.1:8600"
    assert list(config.keys()) == ["/api"]


def test_path_helper_points_inside_the_frontend_dir(tmp_path):
    assert path(tmp_path).parent == tmp_path


def test_module_level_config_is_not_mutated_by_a_call(tmp_path):
    from web.api import proxy_config

    before = json.dumps(proxy_config._CONFIG, sort_keys=True)
    write(tmp_path, 1234)
    after = json.dumps(proxy_config._CONFIG, sort_keys=True)

    assert before == after, "the shared config must stay free of a baked-in port"


def test_no_target_is_baked_into_the_committed_source():
    """The shipped template must not carry a port: that is exactly the drift
    this module exists to remove."""
    from web.api import proxy_config

    assert "8300" not in json.dumps(proxy_config._CONFIG)
