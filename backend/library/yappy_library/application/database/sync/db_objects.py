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

# System schemas are never a target for DDL/SQL execution, so they stay hidden.
_SYSTEM_SCHEMAS_SQL = """
SELECT SCHEMA_NAME
FROM INFORMATION_SCHEMA.SCHEMATA
WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
ORDER BY SCHEMA_NAME
"""

_TABLES_SQL = """
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
  AND TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
ORDER BY TABLE_NAME
"""

_PROCEDURES_SQL = """
SELECT ROUTINE_NAME
FROM INFORMATION_SCHEMA.ROUTINES
WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = 'PROCEDURE'
  AND ROUTINE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
ORDER BY ROUTINE_NAME
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


def list_schemas(conn) -> list[str]:
    """Return the user schemas of the connected database, alphabetically.

    Used to populate the schema picker in the web UI, so the connection may be
    expensive (RDS token + SSM tunnel): callers should not call it per keystroke.
    """
    with conn.cursor() as cur:
        cur.execute(_SYSTEM_SCHEMAS_SQL)
        return [row[0] for row in cur.fetchall()]


def list_tables(conn, schema: str) -> list[str]:
    """Return the base tables of one schema, alphabetically.

    Views are left out on purpose: only ``BASE TABLE`` rows are objects that can
    be compared with ``SHOW CREATE TABLE`` and altered by a compile. Shares
    ``list_schemas``' cost profile — one connection, one query.
    """
    with conn.cursor() as cur:
        cur.execute(_TABLES_SQL, (schema,))
        return [row[0] for row in cur.fetchall()]


def list_procedures(conn, schema: str) -> list[str]:
    """Return the stored procedures of one schema, alphabetically.

    Functions are left out on purpose: ``list_tables`` and this are the two kinds
    ``SHOW CREATE`` and the compile flow understand.
    """
    with conn.cursor() as cur:
        cur.execute(_PROCEDURES_SQL, (schema,))
        return [row[0] for row in cur.fetchall()]


_FK_LINKS_SQL = """
SELECT DISTINCT TABLE_SCHEMA, TABLE_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME
FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
WHERE REFERENCED_TABLE_NAME IS NOT NULL
"""


def foreign_key_links(conn) -> list[tuple[str, str, str, str]]:
    """Every ``(child_schema, child_table, parent_schema, parent_table)`` edge.

    Used to write parents before children when copying rows, so a migration is
    not rejected by a foreign key constraint half way through.
    """
    with conn.cursor() as cur:
        cur.execute(_FK_LINKS_SQL)
        return [
            (row[0], row[1], row[2], row[3])
            for row in cur.fetchall()
            if row[0] and row[1] and row[2] and row[3]
        ]