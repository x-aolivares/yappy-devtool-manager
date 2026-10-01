"""Generation of update scripts. They are meant to be run on the A region to
make it match B (the source of truth)."""

from __future__ import annotations

import re

from . import db_objects as obj
from .diff import ColumnOp


def _strip_definer(sql: str) -> str:
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
    return re.sub(r"\s+", " ", text).strip()


def create_table_script(show_create_b: str) -> str:
    """DDL to create, in A, a table that only exists in B."""
    return show_create_b.strip().rstrip(";")


def create_procedure_script(show_create_b: str) -> str:
    """DDL to create, in A, a procedure that only exists in B (no DEFINER)."""
    return _strip_definer(show_create_b)


def drop_table_script(schema: str, name: str) -> str:
    """DDL to drop from A a table that only exists in A (matching B)."""
    return f"DROP TABLE {obj.quote_ident(schema)}.{obj.quote_ident(name)};"


def drop_procedure_script(schema: str, name: str) -> str:
    """DDL to drop from A a procedure that only exists in A (matching B)."""
    return f"DROP PROCEDURE {obj.quote_ident(schema)}.{obj.quote_ident(name)};"


def replace_procedure_script(show_create_b: str) -> str:
    """DDL to replace, in A, a procedure whose body differs from B."""
    sql = _strip_definer(show_create_b)
    return re.sub(
        r"(?i)^CREATE\s+PROCEDURE\b",
        "CREATE OR REPLACE PROCEDURE",
        sql,
        count=1,
    )


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