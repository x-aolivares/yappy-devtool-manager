"""Generation of update scripts. They are meant to be run on the A region to
make it match B (the source of truth)."""

from __future__ import annotations

import re

from . import db_objects as obj
from .diff import ColumnOp


def _strip_definer(sql: str) -> str:
    """Remove the DEFINER clause while keeping MySQL's own line structure.

    This used to end with ``re.sub(r"\\s+", " ", text)``, which collapsed every
    whitespace run -- newlines included -- and turned a multi-line procedure
    into a single unreadable line. The point of the function is to drop the
    DEFINER, so now that is all it does: line breaks and the parameter
    alignment MySQL emits are left alone, and only whitespace left behind by
    the removal is tidied.
    """
    text = re.sub(
        r"DEFINER\s*=\s*`[^`]+`@`[^`]+`",
        " ",
        sql,
        flags=re.IGNORECASE,
        count=1,
    )
    text = re.sub(
        r"DEFINER\s*=\s*CURRENT_USER",
        " ",
        text,
        flags=re.IGNORECASE,
        count=1,
    )
    # Trailing whitespace per line, and blank-line runs, without joining lines.
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # Removing DEFINER leaves "CREATE   PROCEDURE"; only the header is squeezed,
    # so the alignment inside the parameter list survives.
    return re.sub(r"(?i)^(CREATE)\s+PROCEDURE\b", r"\1 PROCEDURE", text, count=1)


def create_table_script(show_create_b: str) -> str:
    """DDL to create, in A, a table that only exists in B."""
    return show_create_b.strip().rstrip(";")


def drop_table_if_exists_script(schema: str, name: str) -> str:
    """``DROP TABLE IF EXISTS`` for one schema-qualified table.

    Split out from :func:`replace_table_script` because a whole-schema sync emits
    every ``DROP`` first and every ``CREATE`` after: with the tables depending on
    each other, the two halves cannot be interleaved per object. The wording --
    schema-qualified target plus ``IF EXISTS`` -- has to be identical in both
    shapes, so it lives here once.
    """
    target = f"{obj.quote_ident(schema)}.{obj.quote_ident(name)}"
    return f"DROP TABLE IF EXISTS {target};"


def replace_table_script(show_create_b: str, schema: str, name: str) -> str:
    """DDL to replace, in A, a table with B's definition, whatever A has.

    The same shape and the same reason as :func:`replace_procedure_script`: MySQL
    has no ``CREATE OR REPLACE TABLE``, so the only way to make the destination
    match the source exactly is to drop it and create it again.

    The cost, and it is the caller's to own: ``DROP TABLE`` takes the destination's
    rows with it. Compiling a table is a *replace*, not a merge. There is no
    non-destructive alternative left in the UI — whoever wants to keep the rows
    writes the missing ALTERs by hand; this endpoint is the one that says "the
    source is the truth, make the destination look like it".

    The DROP carries ``IF EXISTS`` so one single script works whether or not the
    table is there: the user compiles what they picked without the script having
    to know what it is going to find. That is also what keeps the flow from
    ending in ``1050 Table already exists`` — which is where the editor used to
    leave you when the destination already matched.
    """
    body = create_table_script(show_create_b)
    return f"{drop_table_if_exists_script(schema, name)}\n{body}"


def create_procedure_script(show_create_b: str) -> str:
    """DDL to create, in A, a procedure that only exists in B (no DEFINER)."""
    return _strip_definer(show_create_b)


def drop_table_script(schema: str, name: str) -> str:
    """DDL to drop from A a table that only exists in A (matching B)."""
    return f"DROP TABLE {obj.quote_ident(schema)}.{obj.quote_ident(name)};"


def drop_procedure_script(schema: str, name: str) -> str:
    """DDL to drop from A a procedure that only exists in A (matching B)."""
    return f"DROP PROCEDURE {obj.quote_ident(schema)}.{obj.quote_ident(name)};"


def replace_procedure_script(show_create_b: str, schema: str, name: str) -> str:
    """DDL to replace, in A, a procedure whose body differs from B.

    MySQL has no ``CREATE OR REPLACE PROCEDURE``: that spelling exists for
    views and stored functions, and against a procedure server the parser stops
    at ``PROCEDURE`` with a 1064. Replacing one is ``DROP`` + ``CREATE``, which
    is the documented idiom and is safe here because a procedure holds no data.
    ``_strip_definer`` already leaves the header as ``CREATE PROCEDURE``.
    """
    body = _strip_definer(show_create_b)
    target = f"{obj.quote_ident(schema)}.{obj.quote_ident(name)}"
    return f"DROP PROCEDURE IF EXISTS {target};\n{body}"


def _render_default(row: dict) -> str:
    value = row.get("COLUMN_DEFAULT")
    if value is None:
        return ""
    v = value.strip()
    if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", v):
        return f"DEFAULT {v}"
    if v.upper() in {"CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "NULL"}:
        return f"DEFAULT {v.upper()}"
    if v.endswith("()") or (v.startswith("(") and v.endswith(")")):
        return f"DEFAULT {v}"
    return "DEFAULT '" + v.replace("'", "''") + "'"


def _render_column(row: dict) -> str:
    parts = [obj.quote_ident(row["COLUMN_NAME"]), row["COLUMN_TYPE"]]

    if row.get("CHARACTER_SET_NAME"):
        parts.append(f"CHARACTER SET {row['CHARACTER_SET_NAME']}")
    if row.get("COLLATION_NAME"):
        parts.append(f"COLLATE {row['COLLATION_NAME']}")

    if row.get("IS_NULLABLE") == "NO":
        parts.append("NOT NULL")
    else:
        parts.append("NULL")

    default = _render_default(row)
    if default:
        parts.append(default)

    extra = row.get("EXTRA") or ""
    if extra:
        parts.append(extra)

    comment = (row.get("COLUMN_COMMENT") or "").strip()
    if comment:
        parts.append(f"COMMENT '{comment.replace(chr(39), chr(39) + chr(39))}'")

    return " ".join(parts)


def _index_col(entry: tuple) -> str:
    _, column, sub_part = entry
    return f"{obj.quote_ident(column)}({sub_part})" if sub_part is not None else obj.quote_ident(column)


def _drop_index_clause(name: str) -> str:
    if name == "PRIMARY":
        return "DROP PRIMARY KEY"
    return f"DROP INDEX {obj.quote_ident(name)}"


def _add_index_clause(name: str, non_unique: int, columns: str) -> str:
    if name == "PRIMARY":
        return f"ADD PRIMARY KEY ({columns})"
    kind = "UNIQUE INDEX" if not non_unique else "INDEX"
    return f"ADD {kind} {obj.quote_ident(name)} ({columns})"


def alter_table_script(
    schema: str,
    table: str,
    column_ops: list[ColumnOp],
    index_ops: list[tuple],
) -> str:
    """Build ``ALTER TABLE`` statements that turn A's table into B's."""
    clauses: list[str] = []

    for op in column_ops:
        if op.op == "added":
            clauses.append(f"ADD COLUMN {_render_column(op.definition)}")
        elif op.op == "removed":
            clauses.append(f"DROP COLUMN {obj.quote_ident(op.name)}")
        elif op.op == "modified":
            clauses.append(f"MODIFY COLUMN {_render_column(op.definition)}")

    for op_name, name, sig in index_ops:
        non_unique, entries = sig
        columns = ", ".join(_index_col(entry) for entry in entries)
        if op_name == "added":
            clauses.append(_add_index_clause(name, non_unique, columns))
        elif op_name == "removed":
            clauses.append(_drop_index_clause(name))
        else:
            clauses.append(_drop_index_clause(name))
            clauses.append(_add_index_clause(name, non_unique, columns))

    if not clauses:
        return ""

    target = f"{obj.quote_ident(schema)}.{obj.quote_ident(table)}"
    return f"ALTER TABLE {target}\n  " + ",\n  ".join(clauses) + ";"