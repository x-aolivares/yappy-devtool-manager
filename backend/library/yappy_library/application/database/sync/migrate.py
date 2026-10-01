"""Data migration driven by a SELECT query (Region Sync "Migrar info").

The user writes one query that joins several tables with filters, checks the
result, and then asks to migrate that data to another environment. What they
actually want is *every related table*, restricted to the rows their query
selected — not the flattened join output.

So the query is not rewritten into per-table predicates. The ``FROM`` clause and
the ``WHERE`` clause are reused **verbatim** and only the projection changes:

    SELECT DISTINCT `abc`.* FROM <same FROM> WHERE <same WHERE>

That keeps joins, three-valued logic, correlated subqueries and function calls
exactly as the user wrote them, and ``DISTINCT`` collapses the row multiplication
a join introduces. Every table in the query is migrated on its own terms.

Statements the projection cannot represent faithfully (``GROUP BY``/``HAVING``,
which change what a row *is*, and ``UNION``) are rejected with an explanation
instead of being migrated into something subtly wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pymysql.cursors import DictCursor

from yappy_library.adapters.database.connection import SyncError, connect
from yappy_library.config import Config

from . import db_objects as obj
from .exec import split_statements
from .query import ensure_single_read_statement


class MigrationError(ValueError):
    """Raised when the query cannot be expanded into per-table migrations."""


@dataclass
class TableTarget:
    """One table of the query, plus where its rows have to land."""

    schema: str
    table: str
    alias: str
    #: Overridden by the caller when the destination names schemas differently.
    target_schema: str | None = None
    target_table: str | None = None

    @property
    def name(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def qualifier(self) -> str:
        """Identifier used to project this table's columns in the per-table query."""
        if self.alias:
            return obj.quote_ident(self.alias)
        return obj.quote_ident(self.schema) + "." + obj.quote_ident(self.table)

    @property
    def destination_name(self) -> str:
        schema = self.target_schema or self.schema
        table = self.target_table or self.table
        return f"{schema}.{table}"


@dataclass
class QueryPlan:
    """A query decomposed into the tables that must be migrated.

    ``from_clause``/``where_clause`` are kept verbatim: every per-table query
    reuses them and only swaps the projection, which is what makes the migration
    honour the user's own joins and filters.
    """

    tables: list[TableTarget] = field(default_factory=list)
    from_clause: str = ""
    where_clause: str = ""
    notes: list[str] = field(default_factory=list)


# --- lexical helpers --------------------------------------------------------


def _blank_literals(sql: str) -> str:
    """Blank out quoted text, preserving offsets and length.

    Keyword detection then cannot be fooled by a string containing ``FROM`` or a
    column named ``from``. Backtick-quoted identifiers are blanked too: a table
    called ``from`` must not be mistaken for the clause.
    """
    out = list(sql)
    n = len(sql)
    i = 0
    while i < n:
        if sql[i] not in ("'", '"', "`"):
            i += 1
            continue
        quote = sql[i]
        start = i
        i += 1
        while i < n:
            if sql[i] == "\\" and quote != "`":
                i += 2
                continue
            if sql[i] == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    i += 2
                    continue
                i += 1
                break
            i += 1
        for k in range(start, min(i, n)):
            out[k] = " "
    return "".join(out)


_FROM_RE = re.compile(r"\bFROM\b", re.I)
_WHERE_RE = re.compile(r"\bWHERE\b", re.I)
_TERMINATOR_RE = re.compile(
    r"\b(?:GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|UNION|INTERSECT|EXCEPT|"
    r"PROCEDURE|INTO|WINDOW|FOR\s+(?:UPDATE|SHARE))\b",
    re.I,
)
_JOIN_RE = re.compile(
    r"\b(?:(?:INNER|CROSS|LEFT|RIGHT|FULL|NATURAL|STRAIGHT)\s+)*"
    r"(?:OUTER\s+)?JOIN\b",
    re.I,
)
_ON_RE = re.compile(r"\b(?:ON|USING)\b", re.I)
_GROUP_BY_RE = re.compile(r"\bGROUP\s+BY\b", re.I)
_HAVING_RE = re.compile(r"\bHAVING\b", re.I)
_SET_OP_RE = re.compile(r"\b(?:UNION|INTERSECT|EXCEPT)\b", re.I)
_ORDER_LIMIT_RE = re.compile(r"\b(?:ORDER\s+BY|LIMIT)\b", re.I)

_IDENT = r"(?:`[^`]+`|[A-Za-z_$][\w$]*)"
_TABLE_REF_RE = re.compile(
    rf"^\s*(?P<name>{_IDENT}(?:\s*\.\s*{_IDENT})?)"
    rf"\s*(?:AS\s+)?(?P<alias>{_IDENT})?",
    re.I,
)


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("`") and name.endswith("`") and len(name) >= 2:
        return name[1:-1].replace("``", "`")
    return name


def _find_top_level(masked: str, pattern: re.Pattern[str], start: int = 0) -> int:
    """Index of ``pattern`` outside parentheses, or -1."""
    depth = 0
    for i in range(start, len(masked)):
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = pattern.match(masked, i)
            if m:
                return i
    return -1


def _split_from_clause(masked: str, original: str) -> list[str]:
    """Split the FROM clause at top-level commas and JOIN keywords."""
    bounds: list[int] = [0]
    depth = 0
    i, n = 0, len(masked)
    while i < n:
        c = masked[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            if c == ",":
                bounds.append(i + 1)
                i += 1
                continue
            m = _JOIN_RE.match(masked, i)
            if m:
                # Skip the whole keyword; it is not part of the table reference.
                bounds.append(m.end())
                i = m.end()
                continue
        i += 1
    bounds.append(len(original))

    segments: list[str] = []
    for a, b in zip(bounds, bounds[1:]):
        piece = original[a:b].strip()
        if piece:
            segments.append(piece)
    return segments


def _head_before_join_condition(masked: str, segment: str) -> str:
    """Drop the ``ON ...`` / ``USING (...)`` tail; the table ref is what matters."""
    idx = _find_top_level(masked, _ON_RE)
    return segment[:idx].strip() if idx >= 0 else segment.strip()


# --- parsing ----------------------------------------------------------------


def parse_select(sql: str, default_schema: str = "") -> QueryPlan:
    """Expand a read query into the list of tables that must be migrated.

    Raises :class:`MigrationError` when the statement is not a single query or
    when its shape makes "migrate every related table" ambiguous.
    """
    statement = ensure_single_read_statement(sql)
    masked = _blank_literals(statement)
    notes: list[str] = []

    if _SET_OP_RE.search(masked):
        raise MigrationError(
            "La consulta usa UNION/INTERSECT/EXCEPT. "
            "La migración necesita una consulta simple: "
            "separá las partes y migralas de a una."
        )
    if _GROUP_BY_RE.search(masked) or _HAVING_RE.search(masked):
        raise MigrationError(
            "La consulta tiene GROUP BY o HAVING. "
            "Con agrupación, una fila del resultado no es una fila de la tabla: "
            "quitalos para poder migrar cada tabla según sus filtros."
        )

    from_at = _find_top_level(masked, _FROM_RE)
    if from_at < 0:
        raise MigrationError(
            "La consulta no tiene FROM, así que no referencia ninguna tabla para migrar."
        )
    # `match(..., pos).end()` is already an absolute offset, so use it directly.
    from_start = _FROM_RE.match(masked, from_at).end()

    where_at = _find_top_level(masked, _WHERE_RE, from_start)
    tail_at = _find_top_level(masked, _TERMINATOR_RE, from_start)

    if where_at >= 0 and (tail_at < 0 or where_at < tail_at):
        from_clause = statement[from_start:where_at].strip()
        end = _find_top_level(masked, _TERMINATOR_RE, where_at + 1)
        where_clause = statement[where_at + len("WHERE") : end if end >= 0 else len(statement)].strip()
    else:
        from_clause = statement[from_start : tail_at if tail_at >= 0 else len(statement)].strip()
        where_clause = ""

    if not from_clause:
        raise MigrationError("No se pudo interpretar la cláusula FROM de la consulta.")
    if _ORDER_LIMIT_RE.search(masked):
        notes.append(
            "Se ignoraron ORDER BY y LIMIT: la migración copia todas las filas "
            "que cumplen los filtros, no solo las primeras."
        )

    masked_from = _blank_literals(from_clause)
    tables: list[TableTarget] = []
    seen: set[str] = set()
    for segment in _split_from_clause(masked_from, from_clause):
        head = _head_before_join_condition(_blank_literals(segment), segment)
        if not head:
            continue
        if head.startswith("("):
            raise MigrationError(
                "La consulta usa una tabla derivada (subconsulta en el FROM). "
                "Traé los datos con una consulta simple por tabla."
            )
        m = _TABLE_REF_RE.match(head)
        if not m:
            raise MigrationError(f"No se pudo interpretar la tabla del FROM: '{head}'.")

        # Unquote per part: "`a`.`b`" must not lose only its outer pair.
        parts = [_unquote(p) for p in re.split(r"\s*\.\s*", m.group("name"))]
        alias = _unquote(m.group("alias") or "")
        table = parts[-1]
        schema = parts[-2] if len(parts) > 1 else (default_schema or "").strip()

        if table.upper() == "DUAL":
            continue
        if not schema:
            raise MigrationError(
                f"La tabla '{table}' no tiene schema. "
                "Elegí un schema por defecto en el selector o calificá la tabla."
            )

        key = f"{schema}.{table}"
        if key in seen:
            # The same table joined to itself under two aliases is one table for
            # migration purposes; migrating it twice would be a no-op at best.
            notes.append(
                f"La tabla {key} aparece más de una vez en el FROM; "
                "se migra una sola vez."
            )
            continue
        seen.add(key)
        tables.append(TableTarget(schema=schema, table=table, alias=alias))

    if not tables:
        raise MigrationError("La consulta no referencia ninguna tabla para migrar.")

    return QueryPlan(
        tables=tables,
        from_clause=from_clause,
        where_clause=where_clause,
        notes=notes,
    )


def plan_source(plan: QueryPlan) -> str:
    """The reusable FROM (+ WHERE) text shared by every per-table query."""
    parts = [f"FROM {plan.from_clause}"]
    if plan.where_clause:
        parts.append(f"WHERE {plan.where_clause}")
    return "\n".join(parts)


def table_select(plan: QueryPlan, target: TableTarget) -> str:
    """The query that returns just one table's rows, under the original filters."""
    return f"SELECT DISTINCT {target.qualifier}.*\n{plan_source(plan)}"


def plan_selects(plan: QueryPlan) -> list[tuple[TableTarget, str]]:
    return [(t, table_select(plan, t)) for t in plan.tables]


# --- execution --------------------------------------------------------------


def order_by_dependencies(conn, tables: list[TableTarget]) -> list[TableTarget]:
    """Reorder tables so referenced rows are written before referencing ones.

    ``REPLACE INTO`` still enforces foreign keys, so a child written before its
    parent fails outright. Cycles (which the DB itself forbids) fall back to the
    query's own order.
    """
    by_name = {t.name: t for t in tables}
    remaining = list(tables)
    edges: dict[str, set[str]] = {t.name: set() for t in tables}

    try:
        links = obj.foreign_key_links(conn)
    except Exception:  # noqa: BLE001 - ordering is best-effort, never fatal
        links = []
    for child_schema, child, parent_schema, parent in links:
        child_name = f"{child_schema}.{child}"
        parent_name = f"{parent_schema}.{parent}"
        if child_name in by_name and parent_name in by_name:
            edges[child_name].add(parent_name)

    ordered: list[TableTarget] = []
    while remaining:
        pending = {t.name for t in remaining}
        ready = [t for t in remaining if not (edges[t.name] & pending)]
        if not ready:  # cycle (the DB forbids these): keep the query's order
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        picked = {t.name for t in ready}
        remaining = [t for t in remaining if t.name not in picked]
    return ordered or list(tables)


def count_rows(cfg: Config, sql: str) -> int:
    with connect(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM ({sql}) AS _yappy_count")
            row = cur.fetchone()
    return int(row[0]) if row else 0


@dataclass
class TableMigrationResult:
    schema: str
    table: str
    alias: str
    target_schema: str
    target_table: str
    select_sql: str
    row_count: int = 0
    replaced: int = 0
    skipped_columns: list[str] = field(default_factory=list)
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "schema_name": self.schema,
            "table_name": self.table,
            "alias": self.alias,
            "target_schema": self.target_schema,
            "target_table": self.target_table,
            "select_sql": self.select_sql,
            "row_count": self.row_count,
            "replaced": self.replaced,
            "skipped_columns": self.skipped_columns,
            "ok": self.ok,
            "error": self.error,
        }


def _chunks(rows: list[dict], size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def migrate(
    cfg_source: Config,
    cfg_target: Config,
    plan: QueryPlan,
    batch_size: int = 200,
    dry_run: bool = False,
) -> list[TableMigrationResult]:
    """Copy each table's matching rows from the source into the destination.

    Rows are written with ``REPLACE INTO``, so a row that already exists in the
    destination is replaced by the source's version rather than duplicated.
    """
    batch_size = max(1, min(int(batch_size or 200), 1000))
    results: list[TableMigrationResult] = []

    with connect(cfg_source) as conn_src, connect(cfg_target) as conn_dst:
        ordered = order_by_dependencies(conn_dst, plan.tables)

        for target in ordered:
            select = table_select(plan, target)
            dest_schema = target.target_schema or target.schema
            dest_table = target.target_table or target.table
            result = TableMigrationResult(
                schema=target.schema,
                table=target.table,
                alias=target.alias,
                target_schema=dest_schema,
                target_table=dest_table,
                select_sql=select,
            )

            try:
                dest_columns = {
                    c["COLUMN_NAME"] for c in obj.table_columns(conn_dst, dest_schema, dest_table)
                }
            except Exception:  # noqa: BLE001 - reported per table below
                dest_columns = set()

            if not dest_columns:
                result.ok = False
                result.error = (
                    f"La tabla {dest_schema}.{dest_table} no existe en el ambiente destino."
                )
                results.append(result)
                continue

            try:
                with conn_src.cursor(DictCursor) as cur:
                    cur.execute(select)
                    rows = list(cur.fetchall())
            except Exception as exc:  # noqa: BLE001 - reported per table below
                result.ok = False
                result.error = str(exc)
                results.append(result)
                continue

            result.row_count = len(rows)
            if rows:
                keep = [c for c in rows[0] if c in dest_columns]
                result.skipped_columns = [c for c in rows[0] if c not in dest_columns]
            else:
                keep = []

            if dry_run or not rows:
                results.append(result)
                continue

            if not keep:
                result.ok = False
                result.error = (
                    f"Ninguna columna de {target.name} existe en "
                    f"{dest_schema}.{dest_table}; no se copió ninguna fila."
                )
                results.append(result)
                continue

            column_list = ", ".join(obj.quote_ident(c) for c in keep)
            placeholders = ", ".join(["%s"] * len(keep))
            statement = (
                f"REPLACE INTO {obj.quote_ident(dest_schema)}."
                f"{obj.quote_ident(dest_table)} ({column_list}) VALUES ({placeholders})"
            )

            try:
                with conn_dst.cursor() as cur:
                    for batch in _chunks(rows, batch_size):
                        cur.executemany(
                            statement, [tuple(row.get(c) for c in keep) for row in batch]
                        )
                result.replaced = len(rows)
            except Exception as exc:  # noqa: BLE001 - reported per table below
                result.ok = False
                result.error = str(exc)

            results.append(result)

    position = {t.name: i for i, t in enumerate(plan.tables)}
    results.sort(key=lambda r: position.get(f"{r.schema}.{r.table}", 0))
    return results