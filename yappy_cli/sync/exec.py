"""Execute SQL/DDL against an environment's database (Region Sync "compile" tool).

On Aurora/MySQL "compiling" a stored procedure means running its DDL: the
server (re)compiles it as part of ``CREATE [OR REPLACE] PROCEDURE``. This module
splits pasted code into statements and runs them one by one with autocommit on.

The splitter understands ``BEGIN ... END`` bodies (so internal ``;`` do not cut
a procedure), quoted strings, backtick identifiers, comments and ``DELIMITER``
directives — both plain SQL and mysql-client copies work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator

from ..config import Config
from .conn import SyncError, connect


def split_statements(code: str) -> list[str]:
    """Split SQL text into executable statements.

    Handles ``;`` boundaries outside ``BEGIN ... END`` bodies, quoted strings,
    backticks, ``--``/``#``/``/* */`` comments and ``DELIMITER`` directives.
    """
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(code)
    delimiter = ";"
    custom_active = False
    quote = None
    escaped = False
    depth = 0
    line_start = True

    def flush() -> None:
        text = "".join(buf).strip()
        if text:
            stmts.append(text)
        buf.clear()

    _CLOSERS = {"if", "loop", "while", "repeat", "case"}

    while i < n:
        c = code[i]

        if quote is not None:
            buf.append(c)
            if escaped:
                escaped = False
            elif c == quote:
                if i + 1 < n and code[i + 1] == quote:
                    buf.append(code[i + 1])
                    i += 2
                    continue
                quote = None
            elif c == "\\":
                escaped = True
            i += 1
            continue

        if c == "\n":
            buf.append(c)
            line_start = True
            i += 1
            continue

        if c == "#" or (
            c == "-"
            and i + 1 < n
            and code[i + 1] == "-"
            and (i + 2 >= n or code[i + 2].isspace())
        ):
            while i < n and code[i] != "\n":
                i += 1
            continue

        if c == "/" and i + 1 < n and code[i + 1] == "*":
            while i < n:
                if code[i] == "*" and i + 1 < n and code[i + 1] == "/":
                    i += 2
                    break
                i += 1
            continue

        if c in ("'", '"', "`"):
            quote = c
            escaped = False
            buf.append(c)
            line_start = False
            i += 1
            continue

        if c.isalpha():
            j = i
            while j < n and (code[j].isalnum() or code[j] == "_"):
                j += 1
            word = code[i:j]
            prev_ok = not (
                i > 0 and (code[i - 1].isalnum() or code[i - 1] == "_")
            )
            next_ok = not (
                j < n and (code[j].isalnum() or code[j] == "_")
            )
            if prev_ok and next_ok:
                low = word.lower()
                if low == "begin":
                    depth += 1
                elif low == "end":
                    k, w = j, ""
                    while k < n and code[k].isspace():
                        k += 1
                    p = k
                    while p < n and (code[p].isalnum() or code[p] == "_"):
                        p += 1
                    w = code[k:p].lower()
                    if w in _CLOSERS:
                        pass  # END IF / END LOOP / ... does not close BEGIN blocks
                    elif depth > 0:
                        depth -= 1
                elif low == "delimiter" and line_start:
                    k = j
                    while k < n and code[k].isspace():
                        k += 1
                    m = k
                    while m < n and not code[m].isspace():
                        m += 1
                    delimiter = code[k:m]
                    custom_active = delimiter != ";"
                    while i < n and code[i] != "\n":
                        i += 1
                    buf.clear()
                    line_start = True
                    continue
            buf.append(word)
            line_start = False
            i = j
            continue

        if custom_active:
            if code.startswith(delimiter, i):
                flush()
                i += len(delimiter)
                continue
        elif not depth and c == ";":
            flush()
            i += 1
            continue

        buf.append(c)
        if not c.isspace():
            line_start = False
        i += 1

    flush()
    return stmts


def _preview(sql: str, limit: int = 120) -> str:
    first = next(
        (line.strip() for line in sql.splitlines() if line.strip()),
        sql.strip() or "",
    )
    if len(first) > limit:
        return first[:limit] + "…"
    return first


@dataclass
class StatementResult:
    index: int
    sql: str
    ok: bool
    affected: int | None = None
    ms: float = 0.0
    error: str | None = None


def execute_sql(cfg: Config, schema: str, code: str) -> list[StatementResult]:
    """Run pasted SQL/DDL statements against the environment's database.

    Each statement is executed with autocommit on. Failures are collected per
    statement and do not stop the rest of the script.
    """
    statements = split_statements(code)
    if not statements:
        raise SyncError(
            "No se encontró SQL para ejecutar "
            "(el código está vacío o solo tiene comentarios)."
        )

    results: list[StatementResult] = []

    def _run(conn) -> Iterator[StatementResult]:
        with conn.cursor() as cur:
            if schema:
                cur.execute(
                    "USE `" + schema.replace("`", "``") + "`"
                )
            for idx, stmt in enumerate(statements, start=1):
                start = time.perf_counter()
                try:
                    cur.execute(stmt)
                    ms = (time.perf_counter() - start) * 1000
                    yield StatementResult(
                        index=idx,
                        sql=_preview(stmt),
                        ok=True,
                        affected=cur.rowcount,
                        ms=round(ms, 1),
                    )
                except Exception as exc:  # noqa: BLE001 — report per statement
                    ms = (time.perf_counter() - start) * 1000
                    yield StatementResult(
                        index=idx,
                        sql=_preview(stmt),
                        ok=False,
                        ms=round(ms, 1),
                        error=str(exc),
                    )

    with connect(cfg) as conn:
        for res in _run(conn):
            results.append(res)
    return results