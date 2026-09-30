"""MySQL adapter: introspection, read-only queries, and DDL migration.

Nothing here calls `library/`: the CLI owns the SSM tunnels, and the web only
connects to whatever is listening on DB_PORT. That keeps `kill_ssm()` from ever
seeing a web-owned process.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from pymysql.cursors import Cursor
from pymysql.err import MySQLError

from ..domain.entities import (
    DbConnectionInfo,
    DbObject,
    MigrateObject,
    MigrateResult,
    QueryResult,
    Schema,
)
from ..domain.exceptions import (
    MigrationError,
    ObjectNotFoundError,
    SchemaNotFoundError,
    UnsafeQueryError,
)
from ..domain.sql_guard import assert_read_only
from . import mysql_connection
from .mysql_connection import RdsTokenProvider

if TYPE_CHECKING:  # annotations only; the runtime names are module-qualified
    from .mysql_connection import EnvironmentConnection, LocalConnection

# Module-qualified `mysql_connection.connect(...)` rather than
# `from .mysql_connection import connect`: a directly imported name is bound at
# import time and becomes impossible to substitute in tests.

#: Hard cap on returned rows so a runaway SELECT can't exhaust API memory.
MAX_ROWS = 5000

#: `SHOW CREATE PROCEDURE` embeds DEFINER=`user`@`host`, which does not exist on
#: a developer's machine and makes the statement fail on apply.
_DEFINER_RE = re.compile(
    r"\s*(/\*!\d*\s*)?DEFINER\s*=\s*`(?:[^`]|``)*`@`(?:[^`]|``)*`\s*(/\*!\d*\s*)?",
    re.IGNORECASE,
)
_AUTOC_INC_RE = re.compile(r"\sAUTO_INCREMENT=\d+", re.IGNORECASE)
_SQL_MODE_LINE_RE = re.compile(r"/\*!40101\s+SET\s+@[_A-Za-z0-9]*=?.*?\*/;?", re.IGNORECASE)

#: System schemas that are never worth showing or migrating.
_HIDDEN_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")


class MysqlAdapter:
    """Implements DbRepository, MigrateRepository and DbProbe over PyMySQL."""

    def __init__(self, token_provider: RdsTokenProvider | None = None):
        self._tokens = token_provider or RdsTokenProvider()

    # --- targets --------------------------------------------------------

    def _env_conn(self, env: str) -> EnvironmentConnection:
        return mysql_connection.EnvironmentConnection(env, self._tokens)

    def _local_conn(self) -> LocalConnection:
        return mysql_connection.LocalConnection()

    # --- probe ----------------------------------------------------------

    def probe(self, env: str) -> DbConnectionInfo:
        spec = self._env_conn(env)
        host, port = spec.host(), spec.port()
        reachable, detail = mysql_connection.tcp_probe(host, port)
        return DbConnectionInfo(
            target=spec.label,
            host=host,
            port=port,
            user=_safe(spec.user, ""),
            reachable=reachable,
            detail="" if reachable else detail,
        )

    # --- introspection ---------------------------------------------------

    def list_schemas(self, env: str) -> list[Schema]:
        spec = self._env_conn(env)
        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            rows = cur.fetchall()
        names = [r[0] for r in rows if r and r[0] not in _HIDDEN_SCHEMAS]
        return [Schema(name=n, environment=env) for n in sorted(names)]

    def list_objects(self, env: str, schema: str) -> list[DbObject]:
        spec = self._env_conn(env)
        known = {s.name for s in self.list_schemas(env)}
        if schema not in known:
            raise SchemaNotFoundError(
                f"Schema '{schema}' not found in '{env}'. "
                f"Available: {', '.join(sorted(known)) or 'none'}"
            )

        objects: list[DbObject] = []
        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                (schema,),
            )
            for name, table_type in cur.fetchall():
                if table_type == "BASE TABLE":
                    objects.append(DbObject(name=name, kind="table", schema=schema))
                elif table_type == "VIEW":
                    objects.append(DbObject(name=name, kind="view", schema=schema))

            for routine_type, kind in (
                ("PROCEDURE", "procedure"),
                ("FUNCTION", "function"),
            ):
                cur.execute(
                    "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
                    "WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = %s "
                    "ORDER BY ROUTINE_NAME",
                    (schema, routine_type),
                )
                for (name,) in cur.fetchall():
                    objects.append(DbObject(name=name, kind=kind, schema=schema))

            cur.execute(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = %s ORDER BY TRIGGER_NAME",
                (schema,),
            )
            for (name,) in cur.fetchall():
                objects.append(DbObject(name=name, kind="trigger", schema=schema))

        order = {"table": 0, "view": 1, "procedure": 2, "function": 3, "trigger": 4}
        return sorted(objects, key=lambda o: (order.get(o.kind, 9), o.name))

    # --- queries ---------------------------------------------------------

    def run_query(self, env: str, schema: str, sql: str) -> QueryResult:
        assert_read_only(sql)
        spec = self._env_conn(env)
        started = time.monotonic()
        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]

        return QueryResult(
            columns=columns,
            rows=[_normalise(r) for r in rows],
            row_count=len(rows),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            truncated=truncated,
        )

    def run_local_query(self, sql: str, schema: str | None = None) -> QueryResult:
        assert_read_only(sql)
        spec = self._local_conn()
        started = time.monotonic()
        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            if schema:
                cur.execute(f"USE `{_escape_ident(schema)}`")
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]

        return QueryResult(
            columns=columns,
            rows=[_normalise(r) for r in rows],
            row_count=len(rows),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            truncated=truncated,
        )

    # --- DDL extraction --------------------------------------------------

    def extract_ddl(
        self, env: str, schema: str, objects: tuple[tuple[str, str], ...]
    ) -> list[MigrateObject]:
        spec = self._env_conn(env)
        results: list[MigrateObject] = []
        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            cur.execute(f"USE `{_escape_ident(schema)}`")
            for kind, name in objects:
                ddl, warnings = self._show_create(cur, kind, name, schema)
                results.append(
                    MigrateObject(
                        kind=kind,
                        schema=schema,
                        name=name,
                        ddl=ddl,
                        environment=env,
                        warnings=tuple(warnings),
                    )
                )
        return results

    def _show_create(
        self, cur: Cursor, kind: str, name: str, schema: str
    ) -> tuple[str, list[str]]:
        statement = {
            "table": f"SHOW CREATE TABLE `{_escape_ident(name)}`",
            "view": f"SHOW CREATE VIEW `{_escape_ident(name)}`",
            "procedure": f"SHOW CREATE PROCEDURE `{_escape_ident(name)}`",
            "function": f"SHOW CREATE FUNCTION `{_escape_ident(name)}`",
            "trigger": f"SHOW CREATE TRIGGER `{_escape_ident(name)}`",
        }.get(kind)
        if statement is None:
            raise MigrationError(f"Cannot extract DDL for object kind '{kind}'")

        try:
            cur.execute(statement)
            row = cur.fetchone()
        except MySQLError as e:
            raise ObjectNotFoundError(kind, name, schema) from e

        if not row or len(row) < 2:
            raise ObjectNotFoundError(kind, name, schema)

        raw = row[1]
        ddl, warnings = _sanitise_ddl(kind, raw)
        return ddl, warnings

    # --- DDL apply -------------------------------------------------------

    def apply_ddl(
        self, ddl_objects: list[MigrateObject], target_schema: str
    ) -> MigrateResult:
        spec = self._local_conn()
        failures: list[tuple[str, str]] = []
        executed = 0

        with mysql_connection.connect(spec) as conn, conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{_escape_ident(target_schema)}` "
                f"DEFAULT CHARACTER SET utf8mb4"
            )
            cur.execute(f"USE `{_escape_ident(target_schema)}`")

            for obj in ddl_objects:
                try:
                    cur.execute(obj.ddl)
                    executed += 1
                except MySQLError as e:
                    failures.append((f"{obj.kind} {obj.name}", _short_error(e)))
                except UnsafeQueryError as e:
                    failures.append((f"{obj.kind} {obj.name}", str(e)))
                except Exception as e:  # noqa: BLE001 - report, never abort the batch
                    failures.append((f"{obj.kind} {obj.name}", _short_error(e)))

            conn.commit()

        return MigrateResult(
            applied=tuple(ddl_objects),
            target_schema=target_schema,
            statements_executed=executed,
            failures=tuple(failures),
        )


# --- helpers -------------------------------------------------------------


def _sanitise_ddl(kind: str, raw: str) -> tuple[str, list[str]]:
    """Make DDL from Aurora applicable on a developer's local MySQL.

    Three things reliably break on apply and are stripped here:
      * `DEFINER=`user`@`host`` — that account doesn't exist locally.
      * `AUTO_INCREMENT=n` — a stale counter that overrides local data.
      * the `SET @saved_cs_client` preamble routines carry around.
    """
    ddl = raw
    warnings: list[str] = []

    if "DEFINER" in ddl.upper():
        ddl = _DEFINER_RE.sub(" ", ddl)
        warnings.append("DEFINER clause removed (that account doesn't exist locally)")

    if "AUTO_INCREMENT=" in ddl.upper() and kind in ("table", "view"):
        ddl = _AUTOC_INC_RE.sub("", ddl)
        warnings.append("AUTO_INCREMENT counter removed (it would override local data)")

    if kind in ("procedure", "function", "trigger", "view"):
        if _SQL_MODE_LINE_RE.search(ddl):
            ddl = _SQL_MODE_LINE_RE.sub("", ddl)
            warnings.append("sql_mode preamble removed")

    ddl = ddl.strip().rstrip(";").strip() + ";"
    if not ddl.endswith(";"):
        ddl += ";"
    return ddl, warnings


def _escape_ident(name: str) -> str:
    return name.replace("`", "``")


def _normalise(row: tuple) -> list:
    """JSON-safe cells: bytes/Decimal/date become strings."""
    out = []
    for value in row:
        if isinstance(value, (bytes, bytearray)):
            out.append(value.decode("utf-8", errors="replace"))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out.append(value)
        else:
            out.append(str(value))
    return out


def _short_error(e: Exception) -> str:
    text = str(e)
    return text if len(text) <= 300 else text[:300] + "..."


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - probing must never raise
        return default
