"""Read-only SELECT execution, for the "Ejecutar SQL" page's *consultar* mode.

``exec.execute_sql`` runs DDL/DML and reports per-statement success; this module
answers a different question: "show me the rows". It returns the column names and
a capped list of rows so the browser never receives an unbounded result set.

Only statements that *read* are accepted, plus ``CALL`` for a stored procedure.
A SELECT that turns out to be a write is not something this endpoint should
attempt, so anything else is rejected up front rather than executed.

``CALL`` is the one exception and it is not an oversight: running a stored
procedure is a thing this section has to be able to do, and a procedure is by
nature neither a plain read nor a plain write. What keeps that from being a hole
is that ``CALL`` is named explicitly — ``INSERT``/``UPDATE``/``DELETE`` still do
not get in — and the page says out loud that the procedure can write.
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

#: Server-side cap, in milliseconds, for one interactive query.
#:
#: MySQL aborts the statement with ER_QUERY_TIMEOUT, which reaches the browser as
#: a message that says what happened. A client-side timeout cannot do that: it
#: just gives up, leaving the statement running on the server and the connection
#: in an unknown state. Overridable per environment with ``DB_QUERY_TIMEOUT_MS``;
#: set it to ``0`` to let queries run as long as they take.
DEFAULT_STATEMENT_TIMEOUT_MS = 60_000

_READ_ONLY_HEAD = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|DESC|EXPLAIN)\b", re.I)

#: Statements that *invoke* a stored routine rather than read a table.
#:
#: ``CALL`` is how a procedure is run. It is here and not in ``_READ_ONLY_HEAD``
#: because it is not read-only: a procedure can insert, update and delete, and the
#: person pasting it is doing that on purpose. Letting it through is what makes
#: "Ejecutar SQL" able to run a stored procedure; what keeps it honest is
#: :func:`ensure_runnable_statement`, which rejects everything else and still
#: refuses a bare write.
_ROUTINE_HEAD = re.compile(r"^\s*(CALL|EXEC|EXECUTE)\b", re.I)

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
    """Validate that ``sql`` is one readable statement and return it stripped.

    Accepts ``CALL`` too, for the stored procedures this page now runs: a
    procedure is the one thing here that is neither a plain read nor a plain
    write, and refusing it meant the section could not run one at all.
    """
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
    if _READ_ONLY_HEAD.match(statement) or _ROUTINE_HEAD.match(statement):
        return statement
    raise QueryError(
        "Solo se admiten consultas de lectura (SELECT, WITH, SHOW, DESCRIBE o "
        "EXPLAIN) o la llamada a un stored procedure (CALL). "
        "Para escribir usá la sección Compilar."
    )


def _statement_timeout_ms(cfg) -> int:
    """Read ``DB_QUERY_TIMEOUT_MS``, falling back to the module default.

    ``cfg`` is duck-typed on purpose: the caller's config may be a stub in a
    test, and a missing or unparseable value must degrade to the default rather
    than to an exception.
    """
    getter = getattr(cfg, "get", None)
    raw = getter("DB_QUERY_TIMEOUT_MS") if callable(getter) else None
    if raw is None or not str(raw).strip():
        return DEFAULT_STATEMENT_TIMEOUT_MS
    try:
        return int(str(raw).strip())
    except ValueError:
        return DEFAULT_STATEMENT_TIMEOUT_MS


def _apply_statement_timeout(conn, timeout_ms: int) -> None:
    """Cap the session's statement time so a runaway query ends as an error.

    Best-effort by design. ``max_execution_time`` only exists on MySQL 5.7.8+
    and only binds SELECT, so an older Aurora or a SHOW answers with "Unknown
    system variable"; that is not worth failing the query over, because the
    connection's read timeout is still there as the hard backstop.
    """
    if timeout_ms <= 0:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION max_execution_time = %s", (timeout_ms,))
    except Exception:
        pass


def _first_populated_set(cur, limit: int) -> tuple[list[str], list[dict]]:
    """The first result set that actually has columns, draining the ones before it.

    A stored procedure is the reason this exists. ``CALL`` may answer with several
    result sets in a row — an ``OUT`` parameter here, a status row there, the
    table you cared about last — and ``cur.description`` only ever describes the
    *current* one. Reading just the first would show whatever the procedure
    happened to emit first, which for a procedure that does its work before
    selecting is a row that says nothing.

    So it walks the sets and returns the first one with a description, which is
    the first one with something to show.

    It stops there on purpose. The remaining sets are not drained because the
    connection is opened and closed per request — :func:`connect` yields one
    connection that nothing else reuses — so a set left unread cannot reach
    another query. Draining them would be work for a caller that is about to
    hang up anyway.
    """
    while True:
        fields = cur.description or []
        if fields:
            return [f[0] for f in fields], [_normalize_row(r) for r in cur.fetchmany(limit)]
        if not cur.nextset():
            return [], []


def run_select(cfg: Config, sql: str, limit: int = MAX_ROWS) -> QueryResult:
    """Run one readable statement and return its rows, capped at ``limit``.

    A ``CALL`` goes through here as well: the procedure ran either way, and its
    result set is what there is to show.
    """
    statement = ensure_single_read_statement(sql)
    limit = max(1, min(int(limit or MAX_ROWS), MAX_ROWS))
    is_routine = bool(_ROUTINE_HEAD.match(statement))

    with connect(cfg) as conn:
        _apply_statement_timeout(conn, _statement_timeout_ms(cfg))
        with conn.cursor(DictCursor) as cur:
            start = time.perf_counter()
            try:
                cur.execute(statement)
            except Exception as exc:  # noqa: BLE001 - surfaced as a 400 by the route
                raise SyncError(str(exc)) from exc
            ms = (time.perf_counter() - start) * 1000

            columns, rows = _first_populated_set(cur, limit)
            if is_routine and not columns:
                # A procedure that writes and returns nothing is a legitimate
                # outcome, and reporting it as "0 filas" would read as if the
                # query had found no data. Say what actually happened instead.
                affected = cur.rowcount
                ms = (time.perf_counter() - start) * 1000
                return QueryResult(
                    columns=["mensaje"],
                    rows=[
                        {
                            "mensaje": (
                                f"El procedimiento se ejecutó y no devolvió filas"
                                + (f" ({affected} fila(s) afectadas)." if affected and affected > 0 else ".")
                            )
                        }
                    ],
                    total=1,
                    ms=round(ms, 1),
                )

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