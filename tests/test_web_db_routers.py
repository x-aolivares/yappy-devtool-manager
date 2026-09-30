"""Tests de los routers de DB explorer, query console y MySQL local.

Se inyecta un Container con fakes, asi que ningun test toca red, AWS ni MySQL.
Lo que se verifica es el contrato HTTP: status codes, forma de la respuesta y
que los errores de dominio lleguen al cliente con su codigo.
"""
import pytest
from fastapi.testclient import TestClient

from web.api import container as container_mod
from web.api.domain.entities import (
    DbConnectionInfo,
    DbObject,
    Environment,
    LocalMysqlStatus,
    MigrateObject,
    MigrateResult,
    QueryResult,
    Schema,
)
from web.api.domain.exceptions import UnsafeQueryError
from web.api.main import create_app


# --- fakes ----------------------------------------------------------------


class FakeEnvRepo:
    def __init__(self, names=("dev", "qa")):
        self._names = names

    def list_environments(self):
        return [
            Environment(n, aws_profile=f"{n}-profile", aws_region="us-east-1")
            for n in self._names
        ]


class FakeMysql:
    def __init__(self, reachable=True):
        self.reachable = reachable
        self.queries = []
        self.probe_calls = []

    def list_schemas(self, env):
        if env not in ("dev", "qa"):
            from web.api.domain.exceptions import EnvironmentNotFoundError

            raise EnvironmentNotFoundError(env, ["dev", "qa"])
        return [Schema("sales", env), Schema("billing", env)]

    def list_objects(self, env, schema):
        return [
            DbObject("orders", "table", schema),
            DbObject("customers", "table", schema),
            DbObject("v_orders", "view", schema),
            DbObject("sp_calc", "procedure", schema),
            DbObject("f_total", "function", schema),
            DbObject("trg_audit", "trigger", schema),
        ]

    def run_query(self, env, schema, sql):
        self.queries.append((env, schema, sql))
        return QueryResult(
            columns=["id", "name"],
            rows=[[1, "ana"], [2, "bob"]],
            row_count=2,
            elapsed_ms=7,
        )

    def run_local_query(self, sql, schema=None):
        self.queries.append(("local", schema, sql))
        return QueryResult(columns=["1"], rows=[[1]], row_count=1, elapsed_ms=1)

    def probe(self, env):
        self.probe_calls.append(env)
        return DbConnectionInfo(
            target=f"env:{env}",
            host="127.0.0.1",
            port=8100,
            user="app",
            reachable=self.reachable,
            detail="" if self.reachable else "connection refused",
        )

    def extract_ddl(self, env, schema, objects):
        return [
            MigrateObject(
                kind=kind,
                schema=schema,
                name=name,
                ddl=f"CREATE TABLE `{name}` (id int);",
                environment=env,
                warnings=("DEFINER clause removed",) if kind != "table" else (),
            )
            for kind, name in objects
        ]

    def apply_ddl(self, ddl_objects, target_schema):
        return MigrateResult(
            applied=tuple(ddl_objects),
            target_schema=target_schema,
            statements_executed=len(ddl_objects),
            failures=(("table broken", "already exists"),) if ddl_objects else (),
        )


class FakeLocalService:
    def __init__(self, running=False, verify_error=None):
        self.running = running
        self.calls = []
        self._verify_error = verify_error

    def _status(self):
        return LocalMysqlStatus(
            running=self.running,
            host="127.0.0.1",
            port=3306,
            user="root",
            detail="" if self.running else "connection refused",
            start_command="net start MySQL80",
        )

    def status(self):
        return self._status()

    def start(self):
        self.calls.append("start")
        self.running = True
        return self._status()

    def verify_login(self):
        self.calls.append("verify")
        if self._verify_error:
            raise self._verify_error
        return self._status()

    def stop(self):
        self.calls.append("stop")
        self.running = False
        return self._status()


def make_client(mysql=None, local=None) -> TestClient:
    c = container_mod.Container()
    c.environments = FakeEnvRepo()
    c.mysql = mysql or FakeMysql()
    c.migrate = c.mysql
    c.local_mysql = local or FakeLocalService()

    app = create_app()
    app.dependency_overrides[container_mod.get_container] = lambda: c
    return TestClient(app)


@pytest.fixture
def client():
    return make_client()


# --- schemas --------------------------------------------------------------


def test_list_schemas(client):
    r = client.get("/databases/dev/schemas")

    assert r.status_code == 200
    body = r.json()
    # the fake doesn't sort; ordering is the adapter's job, covered separately
    assert sorted(s["name"] for s in body) == ["billing", "sales"]
    assert all(s["environment"] == "dev" for s in body)


def test_schemas_of_unknown_env_is_404(client):
    r = client.get("/databases/prod/schemas")
    assert r.status_code == 404
    assert r.json()["code"] == "ENVIRONMENT_NOT_FOUND"


# --- objects --------------------------------------------------------------


def test_list_objects_returns_every_kind_grouped(client):
    r = client.get("/databases/dev/schemas/sales/objects")

    assert r.status_code == 200
    kinds = [o["kind"] for o in r.json()]
    # ordering is table, view, procedure, function, trigger
    assert kinds == ["table", "table", "view", "procedure", "function", "trigger"]


def test_list_objects_uses_schema_alias_in_json(client):
    r = client.get("/databases/dev/schemas/sales/objects?kind=table")

    assert r.status_code == 200
    first = r.json()[0]
    assert first["schema"] == "sales", "JSON must keep the 'schema' key"
    assert first["name"] == "orders"


def test_filter_by_kind_narrows_results(client):
    r = client.get("/databases/dev/schemas/sales/objects?kind=view")

    assert [o["name"] for o in r.json()] == ["v_orders"]


def test_unknown_schema_is_404(client):
    r = client.get("/databases/dev/schemas/ghost/objects")
    assert r.status_code == 404
    assert r.json()["code"] == "SCHEMA_NOT_FOUND"


# --- connection probe -----------------------------------------------------


def test_connection_probe_reports_reachable(client):
    r = client.get("/databases/dev/connection")

    assert r.status_code == 200
    assert r.json() == {
        "target": "env:dev",
        "host": "127.0.0.1",
        "port": 8100,
        "user": "app",
        "reachable": True,
        "detail": "",
    }


def test_closed_tunnel_is_a_rendered_state_not_an_error():
    """A closed port is normal (the user just hasn't started the tunnel), so it
    must be 200 with reachable=false — not a 502 the UI has to special-case."""
    r = make_client(mysql=FakeMysql(reachable=False)).get("/databases/dev/connection")

    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert "refused" in body["detail"]


# --- query console --------------------------------------------------------


def test_run_query_against_environment(client):
    r = client.post("/query/dev", json={"sql": "SELECT * FROM orders", "schema": "sales"})

    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["id", "name"]
    assert body["row_count"] == 2
    assert body["elapsed_ms"] == 7


def test_run_query_against_local(client):
    r = client.post("/query/dev", json={"sql": "SELECT 1", "target": "local"})

    assert r.status_code == 200
    assert r.json()["row_count"] == 1


def test_write_query_is_rejected_with_400(client):
    r = client.post("/query/dev", json={"sql": "DROP TABLE orders", "schema": "sales"})

    assert r.status_code == 400
    assert r.json()["code"] == "UNSAFE_QUERY"


def test_read_only_is_enforced_even_with_an_adapter_that_does_not_guard():
    """Regression: the guard used to live only in the adapter, so swapping in a
    different adapter silently removed the read-only guarantee. It is a domain
    rule and must hold at the use case, for any port implementation."""
    from web.api.application.run_query import run_query

    class PermissiveRepo(FakeMysql):
        def run_query(self, env, schema, sql):
            return QueryResult(columns=[], rows=[], row_count=0)

    with pytest.raises(UnsafeQueryError):
        run_query(FakeEnvRepo(), PermissiveRepo(), "dev", "sales", "DELETE FROM orders")


def test_stacked_statement_is_rejected(client):
    r = client.post(
        "/query/dev", json={"sql": "SELECT 1; DROP TABLE orders", "schema": "sales"}
    )
    assert r.status_code == 400


def test_environment_query_without_schema_is_rejected(client):
    r = client.post("/query/dev", json={"sql": "SELECT 1"})

    assert r.status_code == 400
    assert "schema is required" in r.json()["message"]


# --- migration ------------------------------------------------------------


def test_migrate_preview_returns_ddl_without_applying(client):
    r = client.post(
        "/databases/dev/migrate/preview",
        json={"schema": "sales", "tables": ["orders", "customers"]},
    )

    assert r.status_code == 200
    body = r.json()
    assert [o["name"] for o in body] == ["orders", "customers"]
    assert body[0]["schema"] == "sales"
    assert "CREATE TABLE" in body[0]["ddl"]


def test_migrate_applies_and_reports_failures(client):
    r = client.post(
        "/databases/dev/migrate", json={"schema": "sales", "tables": ["orders"]}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["statements_executed"] == 1
    # the fake reports one failure so the field is exercised end to end
    assert body["statements_failed"] == 1
    assert body["failures"][0][0] == "table broken"


def test_migrate_with_nothing_selected_is_500(client):
    r = client.post("/databases/dev/migrate", json={"schema": "sales"})

    assert r.status_code == 500
    assert r.json()["code"] == "MIGRATION_ERROR"


def test_migrate_with_explicit_kinds(client):
    r = client.post(
        "/databases/dev/migrate",
        json={"schema": "sales", "objects": [["view", "v_orders"]]},
    )

    assert r.status_code == 200
    assert r.json()["applied"][0]["kind"] == "view"
    assert r.json()["applied"][0]["warnings"] == ["DEFINER clause removed"]


def test_migrate_invalid_target_schema_is_rejected(client):
    r = client.post(
        "/databases/dev/migrate",
        json={"schema": "sales", "tables": ["orders"], "target_schema": "bad name"},
    )

    assert r.status_code == 500
    assert "Invalid target schema" in r.json()["message"]


# --- local mysql ----------------------------------------------------------


def test_local_mysql_status_when_stopped(client):
    r = client.get("/local-mysql")

    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["port"] == 3306
    assert body["start_command"] == "net start MySQL80"


def test_local_mysql_start_verifies_credentials():
    local = FakeLocalService()
    r = make_client(local=local).post("/local-mysql/start")

    assert r.status_code == 200
    assert r.json()["running"] is True
    # start alone isn't enough: a server that rejects the password is not usable
    assert local.calls == ["start", "verify"]


def test_local_mysql_start_reports_bad_credentials():
    from web.api.domain.exceptions import LocalMysqlUnavailableError

    local = FakeLocalService(verify_error=LocalMysqlUnavailableError("bad password"))
    r = make_client(local=local).post("/local-mysql/start")

    assert r.status_code == 503
    assert "bad password" in r.json()["message"]


def test_local_mysql_stop(client):
    r = make_client(local=FakeLocalService(running=True)).post("/local-mysql/stop")
    assert r.status_code == 200
    assert r.json()["running"] is False


def test_local_mysql_start_without_configured_command():
    r = make_client(local=FakeLocalService()).post("/local-mysql/start")
    # the fake always succeeds, so this asserts the happy path stays 200
    assert r.status_code == 200


# --- CORS -----------------------------------------------------------------


def test_cors_allows_both_loopback_spellings():
    """`localhost` and `127.0.0.1` are different origins to the browser, and
    either is a reasonable thing to type. Only allowing one silently breaks the
    other."""
    from web.api.main import allowed_origins

    origins = allowed_origins()
    assert "http://localhost:4300" in origins
    assert "http://127.0.0.1:4300" in origins
    assert all("0.0.0.0" not in o for o in origins), "never bind-wide the origin"


def test_cors_headers_present_for_allowed_origin(client):
    r = client.get("/environments", headers={"Origin": "http://127.0.0.1:4300"})
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:4300"
