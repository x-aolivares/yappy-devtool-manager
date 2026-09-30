"""Tests de los casos de uso: migracion DDL, resolucion de target, objetos.

La migracion es la parte donde un error cuesta tiempo real (DDL aplicado a
mitades, schemas_TARGET equivocados), asi que se testean las decisiones, no la
conexion.
"""
import pytest

from web.api.application.migrate_object import (
    SUPPORTED_KINDS,
    migrate_ddl,
    preview_ddl,
)
from web.api.application.run_query import run_local_query, run_query
from web.api.domain.entities import (
    Environment,
    MigrateObject,
    MigrateRequest,
    MigrateResult,
    QueryResult,
    Schema,
)
from web.api.domain.exceptions import (
    EnvironmentNotFoundError,
    MigrationError,
    SchemaNotFoundError,
    UnsafeQueryError,
)


class FakeEnvRepo:
    def __init__(self, names=("dev", "qa")):
        self._names = names

    def list_environments(self):
        return [Environment(n) for n in self._names]


class FakeMigrateRepo:
    def __init__(self, schemas=("sales",), ddl_objects=None):
        self._schemas = schemas
        self._ddl = ddl_objects if ddl_objects is not None else []
        self.extracted_for = None

    def list_schemas(self, env):
        return [Schema(s, env) for s in self._schemas]

    def list_objects(self, env, schema):
        return []

    def extract_ddl(self, env, schema, objects):
        self.extracted_for = (env, schema, objects)
        return list(self._ddl)

    def apply_ddl(self, ddl_objects, target_schema):
        return MigrateResult(
            applied=tuple(ddl_objects),
            target_schema=target_schema,
            statements_executed=len(ddl_objects),
            failures=(),
        )


# --- request validation ---------------------------------------------------


def test_rejects_unknown_mode():
    request = MigrateRequest(
        environment="dev", schema="sales", mode="data", tables=("orders",)
    )
    with pytest.raises(MigrationError) as info:
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert "not yet available" in str(info.value)


def test_rejects_nothing_selected():
    request = MigrateRequest(environment="dev", schema="sales")
    with pytest.raises(MigrationError) as info:
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert "at least one" in str(info.value)


def test_rejects_unknown_object_kind():
    request = MigrateRequest(
        environment="dev", schema="sales", objects=(("sequence", "s1"),)
    )
    with pytest.raises(MigrationError) as info:
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert "sequence" in str(info.value)
    assert "table" in str(info.value)


def test_rejects_empty_object_name():
    request = MigrateRequest(environment="dev", schema="sales", objects=(("table", ""),))
    with pytest.raises(UnsafeQueryError):
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)


def test_rejects_unknown_environment():
    request = MigrateRequest(environment="nope", schema="sales", tables=("t",))
    with pytest.raises(EnvironmentNotFoundError):
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)


def test_rejects_unknown_schema():
    request = MigrateRequest(environment="dev", schema="ghost", tables=("t",))
    with pytest.raises(SchemaNotFoundError) as info:
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert "sales" in str(info.value), "should list what is actually available"


# --- object resolution ----------------------------------------------------


def test_bare_table_names_are_treated_as_base_tables():
    repo = FakeMigrateRepo()
    request = MigrateRequest(
        environment="dev", schema="sales", tables=("orders", "customers")
    )

    migrate_ddl(FakeEnvRepo(), repo, request)

    assert repo.extracted_for[2] == (("table", "orders"), ("table", "customers"))


def test_explicit_objects_win_over_bare_tables():
    repo = FakeMigrateRepo()
    request = MigrateRequest(
        environment="dev",
        schema="sales",
        objects=(("view", "v_orders"),),
        tables=("orders",),
    )

    migrate_ddl(FakeEnvRepo(), repo, request)

    assert repo.extracted_for[2] == (("view", "v_orders"),)


def test_every_supported_kind_can_be_requested():
    repo = FakeMigrateRepo()
    request = MigrateRequest(
        environment="dev",
        schema="sales",
        objects=tuple((kind, f"obj_{kind}") for kind in SUPPORTED_KINDS),
    )

    migrate_ddl(FakeEnvRepo(), repo, request)

    assert len(repo.extracted_for[2]) == len(SUPPORTED_KINDS)


# --- target schema resolution ---------------------------------------------


def test_explicit_target_schema_wins(monkeypatch):
    request = MigrateRequest(
        environment="dev", schema="sales", tables=("t",), target_schema="my_local_copy"
    )
    result = migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert result.target_schema == "my_local_copy"


def test_target_defaults_to_env_prefixed_schema(monkeypatch):
    """No LOCAL_DB_NAME configured -> the schema name is namespaced by env, so
    migrating dev and qa don't collide on the same local database."""
    monkeypatch.setattr(
        "web.api.application.migrate_object.Config",
        type("C", (), {"get": staticmethod(lambda *a: None)}),
    )
    request = MigrateRequest(environment="qa", schema="sales", tables=("t",))
    result = migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert result.target_schema == "qa_sales"


def test_configured_local_db_name_wins(monkeypatch):
    monkeypatch.setattr(
        "web.api.application.migrate_object.Config",
        type("C", (), {"get": staticmethod(lambda k, d=None: "yappy_local")}),
    )
    request = MigrateRequest(environment="qa", schema="sales", tables=("t",))
    result = migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert result.target_schema == "yappy_local"


@pytest.mark.parametrize("bad", ["has space", "has-dash", "drop;table", "back`tick"])
def test_invalid_target_schema_is_rejected(bad):
    """The target goes into a CREATE DATABASE identifier, so it must be
    validated rather than escaped — a bad value here is a typo, not an attack."""
    request = MigrateRequest(
        environment="dev", schema="sales", tables=("t",), target_schema=bad
    )
    with pytest.raises(MigrationError) as info:
        migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request)
    assert "Invalid target schema" in str(info.value)


def test_valid_target_schema_variants_accepted():
    for name in ("sales", "my_db", "db2", "_leading"):
        request = MigrateRequest(
            environment="dev", schema="sales", tables=("t",), target_schema=name
        )
        assert (
            migrate_ddl(FakeEnvRepo(), FakeMigrateRepo(), request).target_schema == name
        )


# --- preview vs apply -----------------------------------------------------


def test_preview_does_not_apply():
    """Preview must be side-effect free: it extracts, never writes locally."""
    repo = FakeMigrateRepo(
        ddl_objects=[MigrateObject("table", "sales", "orders", "CREATE TABLE ...;", "dev")]
    )
    applied = []
    repo.apply_ddl = lambda objs, target: applied.append(target)  # type: ignore[assignment]

    objects = preview_ddl(
        FakeEnvRepo(), repo, MigrateRequest(environment="dev", schema="sales", tables=("orders",))
    )

    assert len(objects) == 1
    assert applied == [], "preview must not write to the local MySQL"


# --- query use case -------------------------------------------------------


class FakeDbRepo(FakeMigrateRepo):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.queried = []

    def run_query(self, env, schema, sql):
        self.queried.append((env, schema, sql))
        return QueryResult(columns=["1"], rows=[[1]], row_count=1)


def test_query_requires_known_environment():
    with pytest.raises(EnvironmentNotFoundError):
        run_query(FakeEnvRepo(), FakeDbRepo(), "prod", "sales", "SELECT 1")


def test_query_requires_known_schema():
    with pytest.raises(SchemaNotFoundError):
        run_query(FakeEnvRepo(), FakeDbRepo(), "dev", "ghost", "SELECT 1")


def test_query_is_forwarded_to_the_repo():
    repo = FakeDbRepo()
    result = run_query(FakeEnvRepo(), repo, "dev", "sales", "SELECT 1")

    assert repo.queried == [("dev", "sales", "SELECT 1")]
    assert result.row_count == 1


def test_unsafe_sql_is_rejected_without_touching_the_database():
    """Ordering regression.

    The guard used to run after the schema check, which reaches the database —
    and minting the RDS IAM token. A DROP TABLE therefore came back as an AWS
    credentials error, which points the user at the wrong problem entirely. A
    bad request must fail locally, before any connection is attempted.
    """
    from web.api.domain.exceptions import AwsCredentialsError

    class UnreachableDb:
        def list_schemas(self, env):
            raise AwsCredentialsError("could not mint an RDS token")

        def list_objects(self, env, schema):
            raise AssertionError("must not reach the database")

        def run_query(self, env, schema, sql):
            raise AssertionError("must not reach the database")

    with pytest.raises(UnsafeQueryError) as info:
        run_query(FakeEnvRepo(), UnreachableDb(), "dev", "sales", "DROP TABLE orders")

    assert "not allowed" in str(info.value)


def test_unsafe_sql_is_rejected_even_with_an_unknown_schema():
    """The SQL check does not depend on the schema being valid, so a dangerous
    statement is never worth a round trip."""
    with pytest.raises(UnsafeQueryError):
        run_query(FakeEnvRepo(), FakeDbRepo(), "dev", "ghost", "DELETE FROM orders")


def test_unsafe_local_sql_is_rejected_without_touching_the_database():
    from web.api.domain.exceptions import AwsCredentialsError

    class UnreachableLocal:
        def run_local_query(self, sql, schema=None):
            raise AwsCredentialsError("local connection failed")

    with pytest.raises(UnsafeQueryError):
        run_local_query(UnreachableLocal(), "TRUNCATE orders")
