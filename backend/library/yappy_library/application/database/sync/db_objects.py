"""MySQL/Aurora introspection helpers (tables and stored procedures)."""

from __future__ import annotations

import re

import pymysql
from pymysql.cursors import DictCursor


_COLUMNS_SQL = """
SELECT COLUMN_NAME, ORDINAL_POSITION, COLUMN_TYPE, IS_NULLABLE,
       COLUMN_DEFAULT, EXTRA, CHARACTER_SET_NAME, COLLATION_NAME,
       COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
"""

_INDEXES_SQL = """
SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
ORDER BY INDEX_NAME, SEQ_IN_INDEX
"""


def quote_ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def normalize_ddl(sql: str) -> str:
    """Canonical form for comparison: strips comments, whitespace, keyword case
    and ``DEFINER`` clauses so unrelated formatting does not create false diffs."""
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*--.*$", " ", text)
    text = re.sub(r"DEFINER\s*=\s*`[^`]+`@`[^`]+`", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"DEFINER\s*=\s*CURRENT_USER", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def show_create_table(conn, schema: str, table: str) -> str | None:
    """Return the SHOW CREATE TABLE statement, or None if the table does not exist."""
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"SHOW CREATE TABLE {quote_ident(schema)}.{quote_ident(table)}"
            )
            row = cur.fetchone()
        except pymysql.err.MySQLError:
            return None
    if not row:
        return None
    return row[1] if len(row) > 1 else row[0]


def show_create_procedure(conn, schema: str, name: str) -> str | None:
    """Return the SHOW CREATE PROCEDURE statement, or None if it does not exist."""
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"SHOW CREATE PROCEDURE {quote_ident(schema)}.{quote_ident(name)}"
            )
            row = cur.fetchone()
        except pymysql.err.MySQLError:
            return None
    if not row:
        return None
    return row[2] if len(row) > 2 else row[0]


def table_columns(conn, schema: str, table: str) -> list[dict]:
    with conn.cursor(DictCursor) as cur:
        cur.execute(_COLUMNS_SQL, (schema, table))
        return list(cur.fetchall())


def table_indexes(conn, schema: str, table: str) -> list[dict]:
    with conn.cursor(DictCursor) as cur:
        cur.execute(_INDEXES_SQL, (schema, table))
        return list(cur.fetchall())