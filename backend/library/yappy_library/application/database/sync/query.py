"""Read-only SELECT execution, for the "Ejecutar SQL" page's *consultar* mode.

``exec.execute_sql`` runs DDL/DML and reports per-statement success; this module
answers a different question: "show me the rows". It returns the column names and
a capped list of rows so the browser never receives an unbounded result set.

Only statements that *read* are accepted. A SELECT that turns out to be a write
is not something this endpoint should attempt, so anything else is rejected up
front rather than executed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from decimal import Decimal

from pymysql.cursors import DictCursor

from yappy_library.adapters.database.connection import SyncError, connect
from yappy_library.config import Config

from .exec import split_statements

#: Hard ceiling on rows returned to the browser. The UI paginates client-side.
MAX_ROWS = 500

_READ_ONLY_HEAD = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.I)

#: Statements that read but are not row-returning the way the grid expects.
_NO_ROWS = re.compile(r"^\s*(SHOW\s+(VARIABLES|STATUS|INDEX|CREATE|GRANTS|WARNINGS|ENGINES))\b", re.I)


class QueryError(ValueError):
    """Raised when the pasted text is not a single readable statement."""


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    #: Rows the server reported as matched, when it can be known cheaply.
    total: int | None = None
    truncated: bool = False
    ms: float = 0.0


def _jsonable(value):
    """Normalize a DB-API value into something ``json.dumps`` accepts.

    pymysql hands back ``datetime``/``date``/``Decimal``/``bytes``, none of which
    survive the API's JSON encoder as-is.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"0x{bytes(value).hex()}"
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _normalize_row(row: dict) -> dict:
    return {key: _jsonable(value) for key, value in row.items()}


def ensure_single_read_statement(sql: str) -> str:
    """Validate that ``sql`` is one readable statement and return it stripped."""
    if not sql or not sql.strip():
        raise QueryError("Pegá la consulta que querés ejecutar.")

    statements = split_statements(sql)
    if not statements:
        raise QueryError(
            "No se encontró SQL para ejecutar "
            "(el texto está vacío o solo tiene comentarios)."
        )
    if len(statements) > 1:
        raise QueryError(
            f"La consulta tiene {len(statements)} sentencias. "
            "Para consultar hay que ejecutar una sola a la vez."
        )

    statement = statements[0]
    if not _READ_ONLY_HEAD.match(statement):
        raise QueryError(
            "Solo se admiten consultas de lectura "
            "(SELECT, WITH, SHOW, DESCRIBE o EXPLAIN)."
        )
    return statement


def run_select(cfg: Config, sql: str, limit: int = MAX_ROWS) -> QueryResult:
    """Run one read statement and return its rows, capped at ``limit``."""
    statement = ensure_single_read_statement(sql)
    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS))

    with connect(cfg) as conn:
        with conn.cursor(DictCursor) as cur:
            start = time.perf_counter()
            try:
                cur.execute(statement)
            except Exception as exc:  # noqa: BLE001 - surfaced as a 400 by the route
                raise SyncError(str(exc)) from exc
            ms = (time.perf_counter() - start) * 1000

            fields = cur.description or []
            columns = [f[0] for f in fields]
            rows = [_normalize_row(r) for r in cur.fetchmany(limit)]

    if _NO_ROWS.match(statement):
        # SHOW CREATE/INDEX etc. arrive as one padded row; hand it over verbatim.
        return QueryResult(columns=columns, rows=rows, total=len(rows), ms=round(ms, 1))

    total: int | None = None
    if rows:
        total = len(rows)
        if len(rows) == limit:
            # The page may have more rows than we fetched; say so instead of
            # implying the result set ended here.
            total = None
            return QueryResult(
                columns=columns, rows=rows, total=None, truncated=True, ms=round(ms, 1)
            )
    else:
        total = 0

    return QueryResult(
        columns=columns, rows=rows, total=total, truncated=False, ms=round(ms, 1)
    )