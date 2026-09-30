"""Caso de uso: SQL directo contra un ambiente o contra el MySQL local.

Solo lectura. `sql_guard.assert_read_only` corre en la capa de dominio (y de
nuevo en el adapter, para que ningun camino lo esquive), y el driver va con
multi-statement deshabilitado.
"""
from __future__ import annotations

from ..domain.entities import QueryResult
from ..domain.exceptions import EnvironmentNotFoundError
from ..domain.ports import EnvironmentRepository
from ..domain.sql_guard import assert_read_only
from .list_schemas import require_schema


def run_query(
    env_repo: EnvironmentRepository,
    db_repo,
    env: str,
    schema: str,
    sql: str,
) -> QueryResult:
    known = [e.name for e in env_repo.list_environments()]
    if env not in known:
        raise EnvironmentNotFoundError(env, known)

    # Order matters, and it is not cosmetic.
    #
    # The guard belongs here rather than only in the adapter: it is a domain
    # rule, so enforcing it at the port boundary makes it hold for every
    # adapter. The adapter keeps its own copy as defence in depth.
    #
    # It also has to run *before* `require_schema`, because that validates
    # against the live database. Checking the environment and the SQL first
    # means a malformed request fails locally, without minting an IAM token or
    # opening a connection — otherwise a DROP TABLE comes back as an AWS
    # credentials error, which sends the user off to debug the wrong thing.
    assert_read_only(sql)

    require_schema(env_repo, db_repo, env, schema)
    return db_repo.run_query(env, schema, sql)


def run_local_query(db_repo, sql: str, schema: str | None = None) -> QueryResult:
    assert_read_only(sql)
    return db_repo.run_local_query(sql, schema)
