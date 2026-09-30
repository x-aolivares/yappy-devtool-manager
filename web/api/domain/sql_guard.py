"""Guard de solo-lectura para SQL arbitrario.

Por qué no alcanza con `sql.upper().startswith("SELECT")`:

1. `"-- x\\nDROP TABLE t"` no empieza con SELECT pero sí es destructivo.
2. `"SELECT 1; DROP TABLE t"` sí empieza con SELECT.
3. `"SELECT 'a;b'"` contiene un `;` que NO es separador de sentencias, así que
   un `sql.split(";")` ingenuo rejects consultas perfectly valid.

Por eso el escaneo es character a character y entiende comillas simples,
dobles y backticks: solo cuenta como separador un `;` fuera de un literal.
"""
from __future__ import annotations

from ..domain.exceptions import UnsafeQueryError

#: Statements that cannot mutate data. `WITH` is safe because MySQL only
#: allows CTEs in front of a SELECT.
READ_ONLY_KEYWORDS = frozenset(
    {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"}
)

_MAX_QUERY_CHARS = 100_000


def strip_comments(sql: str) -> str:
    """Remove -- , # and /* */ comments, leaving string literals untouched."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if ch in "'\"`":
            literal, i = _read_literal(sql, i)
            out.append(literal)
            continue

        if ch == "-" and nxt == "-":
            i = _skip_to_eol(sql, i)
            out.append(" ")
            continue

        if ch == "#":
            i = _skip_to_eol(sql, i)
            out.append(" ")
            continue

        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def assert_read_only(sql: str) -> None:
    """Raise UnsafeQueryError unless `sql` is a single read-only statement."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query")

    if len(sql) > _MAX_QUERY_CHARS:
        raise UnsafeQueryError(
            f"Query too long ({len(sql)} chars, max {_MAX_QUERY_CHARS})"
        )

    cleaned = strip_comments(sql).strip()
    if not cleaned:
        raise UnsafeQueryError("Query is only a comment")

    _assert_single_statement(cleaned)

    keyword = _first_keyword(cleaned)
    if keyword is None:
        raise UnsafeQueryError("Could not determine the statement type")
    if keyword not in READ_ONLY_KEYWORDS:
        raise UnsafeQueryError(
            f"'{keyword}' is not allowed. This console is read-only: "
            f"{', '.join(sorted(READ_ONLY_KEYWORDS))}"
        )


# --- internals -----------------------------------------------------------


def _read_literal(sql: str, start: int) -> tuple[str, int]:
    """Return (literal_including_quotes, index_after_closing_quote)."""
    quote = sql[start]
    i = start + 1
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "\\" and quote != "`":
            i += 2
            continue
        if ch == quote:
            # doubled quote is an escaped quote, not the end
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
                continue
            return sql[start : i + 1], i + 1
        i += 1
    return sql[start:], n  # unterminated literal; caller decides


def _skip_to_eol(sql: str, start: int) -> int:
    newline = sql.find("\n", start)
    return len(sql) if newline == -1 else newline + 1


def _assert_single_statement(cleaned: str) -> None:
    """Reject stacked statements. A single trailing ';' is fine."""
    i = 0
    n = len(cleaned)
    while i < n:
        ch = cleaned[i]
        if ch in "'\"`":
            _, i = _read_literal(cleaned, i)
            continue
        if ch == ";":
            if cleaned[i + 1 :].strip():
                raise UnsafeQueryError(
                    "Multiple statements are not allowed — send one query at a time"
                )
            return
        i += 1


def _first_keyword(cleaned: str) -> str | None:
    for token in cleaned.split():
        stripped = token.strip("(`\"'")
        if stripped and (stripped[0].isalpha() or stripped[0] == "_"):
            return stripped.upper()
    return None
