"""Tests del adapter MySQL: sanitizacion de DDL, mapeo de errores, target schema.

No hay MySQL en este entorno, asi que las conexiones se falsean. Lo que se
verifica aqui es la logica pura que hace que un DDL de Aurora sea aplicable en
una maquina de desarrollo: es donde se rompen las migraciones.
"""
import pytest
from pathlib import Path

from web.api.domain.entities import MigrateObject
from web.api.domain.exceptions import DbConnectionError, SchemaNotFoundError
from web.api.infrastructure import mysql_connection as conn_mod
from web.api.infrastructure.mysql_adapter import MysqlAdapter, _sanitise_ddl
from web.api.infrastructure.mysql_connection import (
    EnvironmentConnection,
    LocalConnection,
    RdsTokenProvider,
    connect,
)


# --- DDL sanitisation -----------------------------------------------------


def test_definer_clause_is_stripped():
    """Aurora emits DEFINER=`rdsadmin`@`%`, an account that does not exist on a
    developer's machine, so the statement would fail on apply."""
    raw = (
        "CREATE DEFINER=`rdsadmin`@`%` PROCEDURE `do_thing`()\n"
        "BEGIN\n SELECT 1;\nEND"
    )
    ddl, warnings = _sanitise_ddl("procedure", raw)

    assert "DEFINER" not in ddl
    assert "rdsadmin" not in ddl
    assert "CREATE" in ddl and "PROCEDURE" in ddl
    assert any("DEFINER" in w for w in warnings)


def test_definer_with_versioned_comment_wrapper_is_stripped():
    raw = "/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER x BEGIN END"
    ddl, warnings = _sanitise_ddl("trigger", raw)

    assert "DEFINER" not in ddl
    assert "DEFINER" in ddl or "TRIGGER" in ddl


def test_stale_autoincrement_counter_is_dropped_from_tables():
    """A frozen AUTO_INCREMENT from the source would override local data."""
    raw = (
        "CREATE TABLE `orders` (\n"
        "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB AUTO_INCREMENT=948213 DEFAULT CHARSET=utf8mb4"
    )
    ddl, warnings = _sanitise_ddl("table", raw)

    assert "AUTO_INCREMENT=948213" not in ddl
    # the column-level AUTO_INCREMENT keyword must survive
    assert "AUTO_INCREMENT," in ddl
    assert any("AUTO_INCREMENT" in w for w in warnings)


def test_routine_sql_mode_preamble_is_dropped():
    raw = (
        "/*!40101 SET @saved_cs_client     = @@character_set_client */ ;\n"
        "CREATE DEFINER=`x`@`%` FUNCTION `f`() RETURNS int "
        "DETERMINISTIC SQL SECURITY DEFINER BEGIN RETURN 1; END"
    )
    ddl, _ = _sanitise_ddl("function", raw)

    assert "@saved_cs_client" not in ddl
    assert "DEFINER=" not in ddl
    assert ddl.endswith(";")


def test_plain_ddl_is_left_intact():
    raw = "CREATE TABLE `t` (`id` int NOT NULL, PRIMARY KEY (`id`))"
    ddl, warnings = _sanitise_ddl("table", raw)

    assert ddl.startswith("CREATE TABLE `t`")
    assert ddl.endswith(";")
    assert warnings == []


def test_sanitised_ddl_always_ends_with_exactly_one_semicolon():
    ddl, _ = _sanitise_ddl("table", "CREATE TABLE t (id int);;")
    assert ddl.endswith(";")
    assert not ddl.endswith(";;")


# --- connection specs -----------------------------------------------------


class FakeConfig:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def require(self, key):
        val = self.get(key)
        if val is None:
            raise ValueError(f"missing {key}")
        return val

    @classmethod
    def known_environments(cls):
        return ["dev"]


class StubTokens:
    """Stands in for the IAM token provider so no AWS call is attempted."""

    def __init__(self, token="tok"):
        self._token = token
        self.requested = []

    def get(self, env):
        self.requested.append(env)
        return self._token


def make_env_spec(**overrides):
    """An EnvironmentConnection wired to a fake config, no AWS, no real Config."""
    values = {"DB_PORT": "8100", "AWS_USER": "app", "AWS_HOST": "aurora.x"}
    values.update(overrides)
    spec = EnvironmentConnection("dev", StubTokens())
    spec._cfg = lambda: FakeConfig(values)  # type: ignore[assignment]
    return spec


def test_environment_connection_targets_the_tunnel_localhost():
    spec = make_env_spec()

    assert spec.host() == "127.0.0.1"
    assert spec.port() == 8100
    assert spec.user() == "app"
    assert spec.label == "env:dev"
    assert spec.require_tls() is True, "RDS requires TLS even through the tunnel"


def test_local_connection_defaults_and_no_tls():
    spec = LocalConnection(
        FakeConfig(
            {
                "LOCAL_DB_HOST": "127.0.0.1",
                "LOCAL_DB_PORT": "3307",
                "LOCAL_DB_USER": "dev",
                "LOCAL_DB_PASSWORD": "pw",
            }
        )
    )

    assert (spec.host(), spec.port(), spec.user(), spec.password()) == (
        "127.0.0.1",
        3307,
        "dev",
        "pw",
    )
    assert spec.require_tls() is False
    assert spec.label == "local"


def test_local_connection_can_opt_into_tls():
    spec = LocalConnection(FakeConfig({"LOCAL_DB_SSL": "true"}))
    assert spec.require_tls() is True


def test_environment_connection_uses_the_token_provider():
    tokens = StubTokens("tok-123")
    spec = EnvironmentConnection("qa", tokens)

    assert spec.password() == "tok-123"
    assert tokens.requested == ["qa"], "the token must be minted for this env"


# --- error mapping --------------------------------------------------------


def test_cannot_connect_hints_at_the_tunnel_command(monkeypatch):
    from pymysql.err import OperationalError

    spec = make_env_spec()

    def boom(**kwargs):
        raise OperationalError(2003, "Can't connect to MySQL server on '127.0.0.1'")

    monkeypatch.setattr(conn_mod, "Connection", boom)
    # Port looks open, so the failure surfaces from the driver, not the probe.
    monkeypatch.setattr(conn_mod, "tcp_probe", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: "/tmp/ca.pem")

    with pytest.raises(DbConnectionError) as info:
        connect(spec)

    message = str(info.value)
    assert "yappy run db dev" in message, "must tell the user how to open the tunnel"


def test_closed_port_is_reported_before_the_missing_ca(monkeypatch):
    """The most common failure must not be masked by a secondary config gap.

    A closed tunnel port and a missing CA bundle are both likely; reporting the
    CA would send the user off to fix the wrong thing.
    """
    monkeypatch.setattr(conn_mod, "tcp_probe", lambda *a, **kw: (False, "refused"))

    def must_not_be_called(cfg=None):
        raise AssertionError("CA must not be resolved when the port is closed")

    monkeypatch.setattr(conn_mod, "resolve_ca_path", must_not_be_called)

    with pytest.raises(DbConnectionError) as info:
        connect(make_env_spec())

    assert "yappy run db dev" in str(info.value)


def test_access_denied_points_at_iam_and_expiry(monkeypatch):
    from pymysql.err import OperationalError

    spec = make_env_spec()

    def boom(**kwargs):
        raise OperationalError(1045, "Access denied for user 'app'")

    monkeypatch.setattr(conn_mod, "Connection", boom)
    monkeypatch.setattr(conn_mod, "tcp_probe", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: "/tmp/ca.pem")

    with pytest.raises(DbConnectionError) as info:
        connect(spec)

    assert "IAM" in str(info.value) or "expired" in str(info.value)


def test_environment_connection_verifies_cert_and_disables_multi_statement(monkeypatch):
    """Two guarantees in one place: TLS is verified, stacked statements are off."""
    captured = {}

    class FakeConn:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(conn_mod, "Connection", FakeConn)
    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: "/tmp/ca.pem")
    # The port must look open, otherwise connect() short-circuits before TLS.
    monkeypatch.setattr(conn_mod, "tcp_probe", lambda *a, **kw: (True, ""))

    spec = make_env_spec()

    connect(spec)

    # A dict, not the legacy ssl_ca/ssl_verify_cert pair: an empty dict is what
    # leaves PyMySQL's `_ssl_required` False and lets it fall back to cleartext.
    assert captured["ssl"] == {"ca": "/tmp/ca.pem", "check_hostname": False}
    assert captured["client_flag"] == 0, "multi-statement must stay disabled"
    assert captured["password"] == "tok"


def test_without_a_ca_tls_is_still_required_just_unverified(monkeypatch):
    """The whole point: no CA must degrade verification, never drop encryption.

    The IAM token is the password. PyMySQL's no-options path sets
    `_ssl_required = False` and silently continues in cleartext if the server
    declines SSL, which would leak IAM access on the connection we are trying to
    protect.
    """
    captured = {}

    class FakeConn:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(conn_mod, "Connection", FakeConn)
    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: None)
    monkeypatch.setattr(conn_mod, "tcp_probe", lambda *a, **kw: (True, ""))

    connect(make_env_spec())

    ssl_kwargs = captured["ssl"]
    # Non-empty, which is what flips connections.py:301 `_ssl_required = True`.
    assert ssl_kwargs == {"verify_mode": "none"}


def test_tls_options_reports_whether_the_cert_was_verifiable(monkeypatch):
    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: "/tmp/ca.pem")
    kwargs, verified = conn_mod.tls_options()
    assert verified is True
    assert kwargs["ssl"]["ca"] == "/tmp/ca.pem"

    monkeypatch.setattr(conn_mod, "resolve_ca_path", lambda cfg=None: None)
    kwargs, verified = conn_mod.tls_options()
    assert verified is False, "the UI needs to know it cannot validate the server"
    assert kwargs["ssl"] == {"verify_mode": "none"}


def test_a_configured_but_missing_ca_path_is_still_an_error(monkeypatch):
    """Silence here would hide a typo'd path and quietly downgrade TLS."""
    from web.api.domain.exceptions import ConfigKeyMissingError

    with pytest.raises(ConfigKeyMissingError):
        conn_mod.resolve_ca_path(FakeConfig({"RDS_CA_PATH": "/nonexistent/ca.pem"}))


def test_missing_ca_is_not_an_error(monkeypatch):
    """No CA anywhere degrades verification; it must not block the connection."""
    monkeypatch.setattr(conn_mod.Path, "home", staticmethod(lambda: _EmptyHome()))

    assert conn_mod.resolve_ca_path(FakeConfig({})) is None


class _EmptyHome:
    """Stands in for a home directory with no `.aws` certs in it.

    Diverges to real `Path`s so `.exists()` behaves like production — the CA
    search calls it on the way to deciding a bundle is absent.
    """

    def __truediv__(self, other):
        return Path("/nonexistent-home") / other

    def __str__(self):
        return "/nonexistent-home"


# --- token cache ----------------------------------------------------------


def test_token_is_cached_across_calls(monkeypatch):
    provider = RdsTokenProvider(ttl=1000, margin=0)
    calls = []
    monkeypatch.setattr(
        RdsTokenProvider, "_generate", lambda self, env: calls.append(env) or f"tok-{env}"
    )

    assert provider.get("dev") == "tok-dev"
    assert provider.get("dev") == "tok-dev"
    assert calls == ["dev"], "a cached token must not trigger a new IAM call"


def test_token_is_regenerated_when_close_to_expiry(monkeypatch):
    provider = RdsTokenProvider(ttl=100, margin=90)
    calls = []
    monkeypatch.setattr(
        RdsTokenProvider, "_generate", lambda self, env: calls.append(env) or f"tok-{len(calls)}"
    )

    assert provider.get("dev") == "tok-1"
    # 100s ttl with a 90s margin: anything past 10s of age is stale
    import time as _time

    original = _time.monotonic
    _time.monotonic = lambda: original() + 20
    try:
        assert provider.get("dev") == "tok-2"
    finally:
        _time.monotonic = original

    assert calls == ["dev", "dev"]


def test_token_cache_is_per_environment():
    provider = RdsTokenProvider(ttl=1000, margin=0)
    provider._cache["dev"] = ("tok-dev", 0.0)

    import time as _time

    original = _time.monotonic
    _time.monotonic = lambda: 1.0
    try:
        assert provider.get("dev") == "tok-dev"
        provider._cache["qa"] = ("tok-qa", 1.0)
        assert provider.get("qa") == "tok-qa"
        provider.invalidate("dev")
        assert "dev" not in provider._cache
    finally:
        _time.monotonic = original


# --- apply_ddl resilience -------------------------------------------------


def test_one_failing_object_does_not_abort_the_batch(monkeypatch):
    """Migrating 12 tables and losing 11 to one bad object is the failure mode
    this guards against."""
    from pymysql.err import ProgrammingError

    from web.api.infrastructure import mysql_connection as cmod

    class FakeCursor:
        def __init__(self):
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, *args):
            self.executed.append(sql)
            if "bad" in sql:
                raise ProgrammingError(1146, "Table 'local.bad' doesn't exist")

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

    monkeypatch.setattr(cmod, "connect", lambda spec, **kw: FakeConn())

    objects = [
        MigrateObject("table", "sales", "good", "CREATE TABLE good (id int);", "dev"),
        MigrateObject("table", "sales", "bad", "CREATE TABLE bad (id int);", "dev"),
        MigrateObject("table", "sales", "alsogood", "CREATE TABLE alsogood (id int);", "dev"),
    ]

    result = MysqlAdapter().apply_ddl(objects, "local_sales")

    assert result.statements_executed == 2
    assert len(result.failures) == 1
    assert "table bad" == result.failures[0][0]
    assert result.target_schema == "local_sales"


# --- schema resolution ----------------------------------------------------


def test_list_objects_rejects_unknown_schema():
    from web.api.domain.entities import Schema

    class StubRepo:
        def list_schemas(self, env):
            return [Schema("sales", env)]

        def list_objects(self, env, schema):
            return []

    from web.api.application.list_objects import list_objects

    class EnvRepo:
        def list_environments(self):
            from web.api.domain.entities import Environment

            return [Environment("dev")]

    with pytest.raises(SchemaNotFoundError):
        list_objects(EnvRepo(), StubRepo(), "dev", "nope")
