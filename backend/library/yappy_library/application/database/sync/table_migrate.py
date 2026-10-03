"""Migrate N chosen tables of one schema, each with its own date window.

The second entry point into :mod:`migrate`. ``/api/migrate`` starts from a query
the user wrote; this one starts from a list: "these tables of ``ventas``, and for
the big ones only the rows of January". No join, no alias, no three-valued logic
to reason about — one predicate per table, which is what makes it worth being its
own plan shape instead of asking the user to write the ``SELECT`` by hand.

It only *builds* a plan. The copy loop, the ``REPLACE INTO``, the parent-first
write order and the per-table failure isolation live in :func:`migrate.migrate` and
are not reimplemented here. This module's whole job is to answer "what is the query
for this table" and to refuse, before a single row is written, the requests that
would produce a migration nobody wants.

Three decisions carry the module.

**The window is half-open.** ``date_from`` and ``date_to`` are both inclusive the
way the user reads them, and both are rendered at midnight with the upper bound
*exclusive*:

    `fecha` >= '2026-01-01 00:00:00' AND `fecha` < '2026-02-01 00:00:00'

The tempting ``<= '2026-01-31'`` is wrong for every ``DATETIME``/``TIMESTAMP``
column: midnight of the last day is not the last day, so a window that ends on the
31st silently drops everything from ``00:00:01`` onwards — thousands of rows, no
error, and a migration that looks like it worked. Adding one day to the bound is
the only form that is right for a ``DATETIME``, right for a ``DATE`` (MySQL reads
``2026-01-31 < '2026-02-01 00:00:00'`` as true), and right for the degenerate case
where ``date_from == date_to``, which is then just "that one whole day" instead of a
branch in the renderer. One mechanism, three cases, no special cases.

**The column is read from the source, never taken on faith.** ``date_column`` comes
from the browser, so it is matched against the table's real date columns
(:func:`db_objects.date_columns`) *before* it is quoted into SQL. Two things fall
out of that: a client cannot smuggle arbitrary text into a statement, and a column
that was renamed — or that is simply the wrong type — comes back as a 400 that
names the valid ones instead of a 1054 discovered after three tables were already
replaced.

**An open-ended bound is a real request.** Both bounds, one bound or none are all
accepted. "Everything from March on" and "everything up to February" are ordinary
migrations, and forcing the user to invent the other end — a 1970 sentinel, or a
2099 one — would make this harder to use than the query it replaces. A column with
no bounds at all filters nothing, which is the whole table, and the notes say so.

What this is not:

- It does not create the destination table. A table that is missing there fails that
  table and nothing else; the step that creates it is the schema sync
  (``/api/compile/schema``), and the notes point there.
- It does not delete anything. ``REPLACE INTO`` overwrites the rows it matches by
  primary key and leaves every destination row outside the window exactly where it
  was.
- It is not a transaction. Each table is written on its own, so a failure half way
  through leaves the tables before it already copied — and, within one table, the
  batches before the failing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from yappy_library.adapters.database.connection import SyncError

from . import db_objects as obj
from .migrate import TableMigrationResult, TableTarget

_ONE_DAY = timedelta(days=1)

#: MySQL's errno for "cannot add or update a child row": the parent the row points
#: at is not there. The one failure this feature has to explain rather than report,
#: because with per-table windows it is predictable instead of surprising.
_MISSING_PARENT_ERRNO = 1452


@dataclass
class TableFilter:
    """The date window to copy from one table.

    ``column is None`` means "every row", and it is the only way to say so: a
    column with no bounds is a window that excludes nothing, which is the same
    thing spelled the long way.
    """

    column: str | None = None
    date_from: date | None = None
    #: Inclusive, the way the user reads it. Rendered as an exclusive bound one day
    #: later — see the module docstring for why that is the only safe rendering.
    date_to: date | None = None


@dataclass
class TableListPlan:
    """A list of tables with per-table windows, in the shape the copy loop reads.

    The windows live in a map keyed by :attr:`TableTarget.name` and not on the
    target itself because ``TableTarget`` belongs to the query-driven flow too,
    where "this table's date window" has no meaning to inherit.
    """

    tables: list[TableTarget] = field(default_factory=list)
    #: Per-table window, keyed by ``schema.table``. A target missing from this map
    #: is migrated whole.
    filters: dict[str, TableFilter] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def select_for(self, target: TableTarget) -> str:
        return select_sql(target, self.filters.get(target.name))


# --- rendering --------------------------------------------------------------


def _day_start(day: date) -> str:
    """``day`` at midnight, the way MySQL is handed a date in a comparison."""
    return f"{day.isoformat()} 00:00:00"


def _exclusive_upper(date_to: date | None) -> date | None:
    """``date_to`` as the exclusive day bound, or ``None`` for "no upper bound".

    ``date.max`` has no next day to add — the arithmetic overflows — and nothing in
    MySQL sits above it either, so that one value is already the open-ended bound.
    """
    if date_to is None or date_to >= date.max:
        return None
    return date_to + _ONE_DAY


def render_filter(flt: TableFilter | None) -> str:
    """The ``WHERE`` predicate for one table's window, or ``""`` when it has none.

    Half-open, for the reason in the module docstring: the upper bound is
    ``date_to`` **plus one day** and exclusive. ``date_from == date_to`` is
    therefore "that whole day" with no branch here at all.
    """
    if flt is None or flt.column is None:
        return ""
    column = obj.quote_ident(flt.column)
    parts: list[str] = []
    if flt.date_from is not None:
        parts.append(f"{column} >= '{_day_start(flt.date_from)}'")
    upper = _exclusive_upper(flt.date_to)
    if upper is not None:
        parts.append(f"{column} < '{_day_start(upper)}'")
    return " AND ".join(parts)


def select_sql(target: TableTarget, flt: TableFilter | None) -> str:
    """The query that reads one table's rows, window included.

    ``SELECT *`` with no alias and no ``DISTINCT``, which is the whole difference
    from :func:`migrate.table_select`: there is no join here to multiply rows and no
    projection to narrow, so nothing has to be undone.
    """
    source = f"{obj.quote_ident(target.schema)}.{obj.quote_ident(target.table)}"
    where = render_filter(flt)
    return f"SELECT * FROM {source}" + (f"\nWHERE {where}" if where else "")


# --- planning ---------------------------------------------------------------


def _check_column(conn_b, schema: str, table: str, column: str) -> None:
    """Refuse a ``date_column`` that is not one of *this* table's date columns.

    Checked against the source because that is where the rows come from and where
    the predicate runs; the destination's column list is reconciled later, per row,
    by :func:`migrate.migrate`. The name is matched first and quoted afterwards, so
    nothing the client sent reaches a statement except as a real column of this
    table.
    """
    valid = [c["COLUMN_NAME"] for c in obj.date_columns(conn_b, schema, table)]
    where = f"{schema}.{table}"
    if column in valid:
        return
    if valid:
        raise SyncError(
            f"'{column}' no es una columna de fecha de {where}. "
            f"Columnas DATE/DATETIME/TIMESTAMP de esa tabla: {', '.join(valid)}."
        )
    raise SyncError(
        f"La tabla {where} no tiene columnas DATE/DATETIME/TIMESTAMP en el origen, "
        f"así que no se puede filtrar por '{column}'. Migrá la tabla completa "
        "(sin columna de fecha) o revisá el nombre de la columna."
    )


def build_plan(
    conn_b, schema: str, entries: list[tuple[str, TableFilter]]
) -> TableListPlan:
    """Turn ``[(table, window), ...]`` into a plan, checking it against the source.

    ``conn_b`` is the source and is already open: it is the environment the rows
    come from and the only one that can say which columns are really dates.
    ``entries`` is a list and not a dict so that the response keeps the order the
    user picked the tables in. A ``None`` window is the same as an empty one: the
    table whole.

    Everything checked here is a precondition, so every rejection is a
    :class:`SyncError` raised while nothing has been written yet: the schema and
    each table must exist in the source, and a window's column must be one of that
    table's date columns. The alternative is discovering it per table mid-copy,
    which is a migration that already replaced four tables before it reports that
    the fifth was a typo.
    """
    if schema not in obj.list_schemas(conn_b):
        raise SyncError(
            f"El schema '{schema}' no existe en el origen. "
            "Elegí un schema que exista en el ambiente de origen."
        )

    available = obj.list_tables(conn_b, schema)
    tables: list[TableTarget] = []
    filters: dict[str, TableFilter] = {}
    seen: set[str] = set()
    unbounded: list[str] = []
    notes: list[str] = []

    for raw_table, flt in entries:
        table = (raw_table or "").strip()
        if not table:
            raise SyncError("Hay una tabla sin nombre en la lista de migración.")
        if table not in available:
            raise SyncError(
                f"La tabla {schema}.{table} no existe en el origen. "
                "Elegí tablas que existan en el ambiente de origen."
            )
        flt = flt or TableFilter()

        name = f"{schema}.{table}"
        if name in seen:
            # The same table twice would be copied twice under two windows, and the
            # second REPLACE would silently win.
            notes.append(
                f"La tabla {name} aparece más de una vez en la lista: "
                "se migra una sola vez, con el filtro de la primera."
            )
            continue
        seen.add(name)
        tables.append(TableTarget(schema=schema, table=table, alias=""))

        if flt.column is None:
            continue
        _check_column(conn_b, schema, table, flt.column)
        filters[name] = flt
        if flt.date_from is not None and flt.date_to is not None and flt.date_from > flt.date_to:
            # Rendered as-is this is a window that can never match anything, and
            # "0 filas migradas" reads like "no había datos" rather than "las fechas
            # están al revés". Nothing has been written at this point, so it is
            # still a precondition and not a surprise half way through.
            raise SyncError(
                f"La ventana de {name} está al revés: la fecha inicial "
                f"({flt.date_from.isoformat()}) es posterior a la final "
                f"({flt.date_to.isoformat()}). Revisá el orden."
            )
        if flt.date_from is None and flt.date_to is None:
            unbounded.append(name)

    return TableListPlan(
        tables=tables,
        filters=filters,
        notes=notes + _notes(schema=schema, count=len(tables), unbounded=unbounded),
    )


def _notes(schema: str, count: int, unbounded: list[str]) -> list[str]:
    """The Spanish warnings that always travel with a table-list migration.

    Built here and not in the route, for the reason
    :func:`schema_sync._notes` builds its own: the library is what knows what the
    copy does to the destination, and every caller — this endpoint today, the CLI
    tomorrow — owes the user the same warnings.
    """
    notes = [
        f"Se migran {count} tabla(s) de '{schema}' con REPLACE INTO: una fila que ya "
        "está en el destino se reemplaza por la del origen cuando coinciden en la "
        "primary key.",
        "REPLACE INTO no borra nada: las filas del destino que están fuera de la "
        "ventana siguen ahí y conviven con las copiadas. Si el destino tiene que "
        "quedar con el contenido de la ventana y nada más, borrá esas filas antes.",
        "La tabla tiene que existir en el destino: esta migración no la crea. El paso "
        "que la crea es la sincronización de esquema (/api/compile/schema).",
        "No hay transacción: cada tabla se escribe por separado y cada lote se "
        "confirma solo, así que una falla no deshace lo que ya se copió — ni lo que "
        "ya se copió de esa misma tabla. El destino queda migrado a medias.",
    ]
    for name in unbounded:
        notes.append(
            f"La tabla {name} tiene columna de fecha elegida pero ninguna fecha "
            "indicada: el filtro no acota nada y se migra completa."
        )
    return notes


# --- explaining the predictable failure --------------------------------------


def missing_parent_notes(results: list[TableMigrationResult]) -> list[str]:
    """Say what a foreign key rejection (1452) actually means here, per table.

    With one window per table this failure is not a surprise to be logged, it is
    the expected consequence of a design the user chose: a child row can point at a
    parent that the parent's own window left behind, because the two tables are
    almost never filtered by the same column. A row written in March can reference
    an order from January, so windowing both by their own ``fecha`` cuts the parent
    out from under the child, and the destination — which enforces the constraint —
    refuses the row.

    So the note does not paraphrase the driver. It names the cause and the two ways
    out, because the migrator cannot pick one: only the caller knows whether the
    window or the parent table is what should change.

    Anything that is not a 1452 keeps the plain error it already has; this is not a
    generic handler wearing a specific message.
    """
    notes: list[str] = []
    for result in results:
        if result.ok or result.error_errno != _MISSING_PARENT_ERRNO:
            continue
        notes.append(
            f"La tabla {result.schema}.{result.table} no se migró: el destino rechazó "
            "filas con una foreign key (1452). Es lo que pasa cuando la columna de "
            "fecha del padre no es la misma que la del hijo, o cuando la ventana no "
            "alcanza hacia atrás: la tabla tiene filas que apuntan a padres que "
            "quedaron fuera del rango y en el destino no están. Se resuelve "
            "ampliando el rango hasta que incluya esos padres, o migrando el padre "
            "completo (sin filtro de fecha) en la misma corrida."
        )
    return notes
