"""La configuración: una clave, un significado, y los defaults en un solo lugar.

Tres cosas que se arreglan acá:

1. **Una clave escrita sin valor tapaba el entorno.** `AWS_PROFILE=` —sin nada
   atrás— vale `None` en el dotenv, y el `get` devolvía `None` sin llegar a los
   overrides. O sea, `require()` fallaba con "Missing required config" aunque la
   variable estuviera seteada en el entorno, que es justo para lo que el
   override existe.

2. **`DB_PORT` significaba dos cosas.** Es el puerto local del túnel SSM, y
   también el puerto de MySQL de la conexión directa — con defaults distintos
   (8100 y 3306)—, y el template declaraba las dos en el mismo archivo con la
   misma clave.

3. **`"53360"` estaba copiado en ocho lugares.** Un default duplicado en la
   library y en el CLI son dos números que van a divergir sin que nadie lo note.
"""

from __future__ import annotations

import pytest

from yappy_library.config import (
    DEFAULT_AWS_PORT,
    DEFAULT_MYSQL_PORT,
    Config,
    aws_port_of,
    mysql_port_of,
)


class _Cfg:
    """Un doble mínimo: lo que las funciones necesitan es un `get`."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# --- Config.get ------------------------------------------------------------


def test_una_clave_sin_valor_no_tapa_el_entorno(tmp_path, monkeypatch):
    """`AWS_PROFILE=` sin nada atrás es `None` en el dotenv, no una cadena vacía."""
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_PROFILE\n", encoding="utf-8")
    monkeypatch.setenv("AWS_PROFILE", "del-entorno")

    cfg = Config()

    assert cfg.get("AWS_PROFILE") == "del-entorno"
    assert cfg.profile == "del-entorno"


def test_una_clave_sin_valor_no_tapa_el_override(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_PROFILE\n", encoding="utf-8")
    monkeypatch.setenv("YAPPY_AWS_PROFILE", "del-override")

    cfg = Config()

    assert cfg.get("AWS_PROFILE") == "del-override"


def test_una_clave_con_valor_vacio_sigue_vacia(tmp_path, monkeypatch):
    """`AWS_PROFILE=` (con el `=`) sí es un valor: la cadena vacía.

    Distinguir "no está" de "está pero vacío" importa: en el template hay
    claves vacías a propósito, como `BITBUCKET_TOKEN=`.
    """
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_PROFILE=\n", encoding="utf-8")
    monkeypatch.setenv("AWS_PROFILE", "del-entorno")

    cfg = Config()

    assert cfg.get("AWS_PROFILE") == ""


def test_require_no_falla_con_el_valor_en_el_entorno(tmp_path, monkeypatch):
    """El `require` es donde el bug se veía: decía que faltaba la config."""
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("AWS_INSTANCE\n", encoding="utf-8")
    monkeypatch.setenv("AWS_INSTANCE", "i-123")

    cfg = Config()

    assert cfg.require("AWS_INSTANCE") == "i-123"


# --- los puertos -----------------------------------------------------------


def test_el_puerto_de_mysql_viene_de_db_mysql_port():
    cfg = _Cfg({"DB_MYSQL_PORT": "3307"})

    assert mysql_port_of(cfg) == 3307


def test_el_puerto_de_mysql_acepta_db_port_por_compatibilidad():
    """Un environment que ya tenía `DB_PORT` no puede dejar de funcionar."""
    cfg = _Cfg({"DB_PORT": "3307"})

    assert mysql_port_of(cfg) == 3307


def test_el_puerto_de_mysql_por_defecto_es_3306():
    assert mysql_port_of(_Cfg({})) == 3306
    assert DEFAULT_MYSQL_PORT == "3306"


def test_db_mysql_port_manda_sobre_db_port():
    """Cuando están los dos, gana el que dice "MySQL"."""
    cfg = _Cfg({"DB_PORT": "8100", "DB_MYSQL_PORT": "3307"})

    assert mysql_port_of(cfg) == 3307


def test_el_puerto_local_del_tunel_sigue_siendo_el_de_db_port(tmp_path, monkeypatch):
    """`DB_PORT` sigue siendo el puerto local del túnel SSM."""
    monkeypatch.setattr(Config, "_config_dir", tmp_path)
    (tmp_path / "env.base").write_text("DB_PORT=8101\n", encoding="utf-8")

    cfg = Config()

    assert cfg.db_port == 8101


def test_el_puerto_de_aurora_tiene_un_default_en_un_solo_lugar():
    assert aws_port_of(_Cfg({})) == 53360
    assert DEFAULT_AWS_PORT == "53360"


def test_el_puerto_de_aurora_se_puede_configurar():
    assert aws_port_of(_Cfg({"AWS_PORT": "5432"})) == 5432


@pytest.mark.parametrize(
    "cfg",
    [
        _Cfg({}),
        _Cfg({"AWS_PORT": "53360"}),
    ],
)
def test_el_puerto_de_aurora_no_depende_de_como_se_haya_creado_la_config(cfg):
    """Da igual si la config es un `Config` o un doble con `get`."""
    assert aws_port_of(cfg) == 53360


# --- el usuario de la base --------------------------------------------------


def test_el_usuario_de_la_base_acepta_db_user():
    """`DB_USER` es el nombre explícito de la base, y vale."""
    from yappy_library.adapters.database.credentials import resolve_db_user

    assert resolve_db_user(_Cfg({"DB_USER": "app"})) == "app"


def test_el_usuario_de_la_base_acepta_aws_user():
    from yappy_library.adapters.database.credentials import resolve_db_user

    assert resolve_db_user(_Cfg({"AWS_USER": "app"})) == "app"


def test_el_usuario_de_la_base_prefiere_db_user():
    from yappy_library.adapters.database.credentials import resolve_db_user

    cfg = _Cfg({"DB_USER": "app", "AWS_USER": "otro"})

    assert resolve_db_user(cfg) == "app"


def test_el_usuario_de_la_base_acepta_el_env_local():
    from yappy_library.adapters.database.credentials import resolve_db_user

    assert resolve_db_user(_Cfg({}), {"DB_USER": "del-local"}) == "del-local"


def test_el_token_no_exige_aws_user_si_hay_db_user(monkeypatch):
    """El caso que rompía: un environment con sólo `DB_USER` fallaba al pedir el token.

    La conexión aceptaba `DB_USER` y el token exigía `AWS_USER`, así que un
    environment configurado como documenta el template —con `DB_USER`— no
    llegaba nunca a conectarse.
    """
    from yappy_library.adapters.database import credentials

    capturadas = {}

    class _Client:
        def generate_db_auth_token(self, **kwargs):
            capturadas.update(kwargs)
            return "token"

    class _Session:
        def create_client(self, *args, **kwargs):
            return _Client()

    monkeypatch.setattr(credentials.botocore.session, "Session", lambda *a, **k: _Session())

    class _Cfg2:
        profile = "base-profile"

        def get(self, key, default=None):
            return {
                "AWS_REGION": "us-west-2",
                "AWS_HOST": "db.example.com",
                "DB_USER": "app",
            }.get(key, default)

        def require(self, key):
            value = self.get(key)
            if value is None:
                raise ValueError(key)
            return value

    token = credentials.generate_token(_Cfg2())

    assert token == "token"
    assert capturadas["DBUsername"] == "app"
