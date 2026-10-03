"""Assemble ONE script that takes a whole schema from the source (B) into the
destination (A) — the bulk sibling of the single-object compile.

``/api/compile`` covers one table or one procedure at a time, and it is the right
tool for a table whose destination definition drifted. It is the wrong tool for
"this schema has to look like it does over there": a schema holds dozens of
objects, they reference each other, and a script per object is a script per
object that fails halfway when two of them are related.

That is the whole problem, and it is an **order** problem, which is why this
module spends its weight on the two orderings instead of on the SQL text:

``drop_order``
    Children before parents, walked over the foreign key graph of the
    **destination**. Those are the constraints that are actually alive when the
    script runs: MySQL refuses to drop a table another one references (3730),
    and ``IF EXISTS`` does not help because it only silences "unknown table"
    (1051).

``create_order``
    Parents before children, walked over the foreign key graph of the
    **source**. Those are the constraints every incoming ``CREATE TABLE``
    declares, so a child created before its parent does not exist fails on the
    statement itself.

The two graphs are genuinely different objects and are deliberately read from
different environments. A destination can have constraints the source never had
(a leftover FK between two tables the source now keeps unrelated) and the source
can have ones the destination is about to stop having; using one graph for both
orders gets exactly one of the two lists right.

Everything here is generate-only: :func:`plan` and :func:`build_script` read both
environments and return text. Nothing is executed, here or anywhere near here.

What the script is NOT:

- It does not delete what the destination has and the source lacks. Those show up
  in :attr:`SchemaPlan.left_alone` and are left untouched on purpose — a schema
  sync says "make my copy of what the origin has match", not "make the
  destination be an exact clone of the origin".
- It is not a transaction. ``execute_sql`` runs with autocommit and does not stop
  on a failed statement, so a failure half way leaves the destination partially
  migrated: the drops before the failure are already applied. That is why the
  notes are built up front and always travel with the response.

``FOREIGN_KEY_CHECKS=0`` is deliberately not used anywhere. It is the tempting
one-liner for "make the order problem go away", and it buys that by dropping the
foreign keys from every *child* table it is applied to, permanently and silently
— the destination ends up with constraints the source never had, which is the
opposite of what this feature promises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from yappy_library.adapters.database.connection import SyncError

from . import db_objects as obj
from . import ddl


@dataclass
class SchemaPlan:
    """What the script will do, before a single character of it is written.

    ``tables``/``procedures`` are the objects in scope, alphabetical. The two
    orders are permutations of ``tables``: the script's tables section is
    *all* the drops in ``drop_order`` followed by *all* the creates in
    ``create_order``, so the two directions cannot be satisfied one table at a
    time.
    """

    schema: str
    #: True when the destination does not have the schema yet, so the script has
    #: to create it before anything can land in it.
    create_schema: bool
    tables: list[str] = field(default_factory=list)
    procedures: list[str] = field(default_factory=list)
    drop_order: list[str] = field(default_factory=list)
    create_order: list[str] = field(default_factory=list)
    #: Base tables the destination has and the source does not. Reported, never
    #: dropped.
    left_alone: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- ordering ---------------------------------------------------------------


def _ordered(tables: list[str], blockers: dict[str, set[str]]) -> list[str]:
    """Order ``tables`` so that everything in ``blockers[t]`` comes before ``t``.

    ``blockers[t]`` holds the names ``t`` has to wait for — its parents when it is
    being created, its children when it is being dropped. Kahn's algorithm over
    the names in ``tables``: a table is ready once none of
    the tables that block it are still pending. Pure — no connection, no I/O — so
    the ordering is asserted directly in tests instead of being read off a script.

    On a cycle nothing is ever ready. MySQL forbids those, so reaching the
    fallback means the graph is corrupt rather than the schema being exotic, and
    the least surprising thing to do is keep the input order instead of looping.
    This is the same fallback :func:`migrate.order_by_dependencies` uses when it
    cannot find a valid write order.
    """
    remaining = list(tables)
    pending = set(remaining)
    ordered: list[str] = []
    while remaining:
        ready = [t for t in remaining if not (blockers.get(t, set()) & pending)]
        if not ready:  # cycle: keep the caller's order rather than looping forever
            ordered.extend(remaining)
            break
        ordered.extend(ready)
        for name in ready:
            pending.discard(name)
        remaining = [t for t in remaining if t in pending]
    return ordered


def _in_scope_children(
    links: list[tuple[str, str, str, str]],
    schema: str,
    tables: list[str],
) -> dict[str, set[str]]:
    """Narrow ``foreign_key_links`` to the edges that constrain *this* script.

    Both endpoints have to be in scope and both have to live in ``schema``. An
    edge whose parent is out of scope says nothing about our order — that table
    already exists in the destination and this script does not touch it, so the
    child is free to be dropped or created whenever. An edge from *another*
    schema is dropped for the same reason, and is reported by
    :func:`_cross_schema_inbounds` rather than silently ignored.
    """
    in_scope = set(tables)
    children: dict[str, set[str]] = {}
    for child_schema, child, parent_schema, parent in links:
        if (
            child_schema == schema
            and parent_schema == schema
            and child in in_scope
            and parent in in_scope
        ):
            children.setdefault(parent, set()).add(child)
    return children


def _cross_schema_inbounds(
    links: list[tuple[str, str, str, str]],
    schema: str,
    tables: list[str],
) -> list[str]:
    """Schemas outside ``schema`` that reference one of ``tables``.

    Worth saying out loud, because the script cannot do anything about it: that
    child table is not ours to drop, not ours to reorder around, and it stays
    pointing at a table this script is about to replace. The note names the
    schema so the user can decide before running anything.
    """
    in_scope = set(tables)
    outside: set[str] = set()
    for child_schema, _child, parent_schema, parent in links:
        if child_schema != schema and parent_schema == schema and parent in in_scope:
            outside.add(child_schema)
    return sorted(outside)


def _parents_of(children: dict[str, set[str]]) -> dict[str, set[str]]:
    """Transpose a parent -> children adjacency into child -> parents."""
    return {child: {parent} for parent, kids in children.items() for child in kids}


def _orders(
    schema: str,
    tables: list[str],
    destination_links: list[tuple[str, str, str, str]],
    source_links: list[tuple[str, str, str, str]],
) -> tuple[list[str], list[str], list[str]]:
    """The two orders of the script, plus the foreign schemas pointing at us.

    Destination graph and source graph, opposite directions — see the module
    docstring for why they are not the same walk:

    - a table is dropped only after every in-scope child of it is gone, so the
      blockers of a parent are its children;
    - a table is created only after every in-scope parent of it exists, so the
      blockers of a child are its parents.
    """
    destination_children = _in_scope_children(destination_links, schema, tables)
    source_children = _in_scope_children(source_links, schema, tables)
    drop_order = _ordered(tables, destination_children)
    create_order = _ordered(tables, _parents_of(source_children))
    return drop_order, create_order, _cross_schema_inbounds(destination_links, schema, tables)


# --- planning ---------------------------------------------------------------


def plan(
    conn_b,
    conn_a,
    schema: str,
    include_tables: bool,
    include_procedures: bool,
) -> SchemaPlan:
    """Decide what the script will contain, reading both open connections.

    Neither connection is opened here: both are already up, and the whole point
    of this endpoint is that looking is cheap and writing is not done at all.
    ``conn_b`` is the source (the truth), ``conn_a`` the destination.

    Raises :class:`SyncError` when ``schema`` does not exist in the source —
    there is nothing to take from a schema that is not there, and reporting it
    here costs the user a red box instead of a half-applied script.

    Unlike :func:`migrate.order_by_dependencies`, reading the foreign key graphs
    is *not* best-effort: a failed read would silently produce an order that dies
    with 3730 in the middle of the drops, so it is better to answer with an
    error while nothing has been applied.
    """
    if schema not in obj.list_schemas(conn_b):
        raise SyncError(
            f"El schema '{schema}' no existe en el origen. "
            "Elegí un schema que exista en el ambiente de origen."
        )

    create_schema = schema not in obj.list_schemas(conn_a)
    tables = sorted(obj.list_tables(conn_b, schema)) if include_tables else []
    procedures = sorted(obj.list_procedures(conn_b, schema)) if include_procedures else []

    # Everything the destination has that is not in scope stays exactly as it is.
    # Only meaningful when tables are in scope at all: on a procedures-only run
    # *every* destination table is technically "not in scope", and reporting them
    # as "tables the origin does not have" would be a lie -- the origin almost
    # certainly has them, the user simply did not ask for tables this time. The
    # note built from this list reads exactly that, so the list stays empty.
    left_alone = (
        sorted(set(obj.list_tables(conn_a, schema)) - set(tables))
        if include_tables
        else []
    )

    if tables:
        drop_order, create_order, cross_schema = _orders(
            schema,
            tables,
            obj.foreign_key_links(conn_a),
            obj.foreign_key_links(conn_b),
        )
    else:
        # Nothing to order, and on a brand new schema the graph query is a scan of
        # a database that does not even have the schema yet.
        drop_order, create_order, cross_schema = [], [], []

    return SchemaPlan(
        schema=schema,
        create_schema=create_schema,
        tables=tables,
        procedures=procedures,
        drop_order=drop_order,
        create_order=create_order,
        left_alone=left_alone,
        notes=_notes(
            schema=schema,
            create_schema=create_schema,
            tables=tables,
            procedures=procedures,
            left_alone=left_alone,
            cross_schema=cross_schema,
        ),
    )


def _notes(
    *,
    schema: str,
    create_schema: bool,
    tables: list[str],
    procedures: list[str],
    left_alone: list[str],
    cross_schema: list[str],
) -> list[str]:
    """The Spanish warnings that always travel with the script.

    Keyword-only on purpose: four of the six arguments are lists of strings, and
    a swapped pair would produce plausible notes about the wrong objects.

    They are built once, here, instead of in the route: the library is the one
    that knows what the script does to the destination, and every caller — the
    endpoint today, the CLI tomorrow — owes the user the same warnings.
    """
    notes: list[str] = []
    if create_schema:
        notes.append(
            f"El schema '{schema}' no existe en el destino: el script lo crea con "
            "CREATE DATABASE IF NOT EXISTS."
        )
    if tables:
        notes.append(
            f"Se reemplazan {len(tables)} tabla(s) de '{schema}' con la definición del "
            "origen. Cada DROP TABLE se lleva las filas que el destino tenga en esa "
            "tabla: el script no las migra."
        )
    if procedures:
        notes.append(
            f"Se reemplazan {len(procedures)} stored procedure(s) de '{schema}' con la "
            "definición del origen (sin DEFINER: se crea con el usuario que corre el "
            "script)."
        )
    if left_alone:
        notes.append(
            f"El destino tiene {len(left_alone)} tabla(s) que el origen no tiene "
            f"({', '.join(left_alone)}): no se tocan y quedan como están."
        )
    if not tables and not procedures:
        notes.append(
            f"El origen no tiene tablas ni stored procedures en '{schema}': "
            "el script no reemplaza nada."
        )
    if cross_schema:
        notes.append(
            f"Ojo: tablas de {', '.join(cross_schema)} referencian a tablas de "
            f"'{schema}' con foreign keys. El script no las puede reordenar ni "
            "tocarlas: si el destino reemplaza una de esas tablas, esas filas quedan "
            "sin padre."
        )
    notes.append(
        "No hay transacción: el script corre con autocommit y no corta ante un "
        "statement fallido. Si algo falla a mitad de camino, el destino queda "
        "migrado a medias (los DROP anteriores ya se aplicaron)."
    )
    return notes


# --- script -----------------------------------------------------------------


def _terminate(statement: str) -> str:
    """End a statement with exactly one ``;``.

    ``SHOW CREATE TABLE`` and ``SHOW CREATE PROCEDURE`` do not emit a trailing
    semicolon — they do not need one, because they are the whole answer. The
    single-object compile gets away with that because its ``CREATE`` is always
    the last thing in the script. This one is a sequence of many statements, and
    :func:`exec.split_statements` splits on ``;``: without a terminator, two
    consecutive ``CREATE TABLE`` bodies arrive at MySQL glued into a single
    statement and die with a 1064 pointing at the *second* one.

    ``rstrip(";")`` first so a source that did emit one does not turn into ``;;``.
    """
    return statement.rstrip().rstrip(";") + ";"


def _table_ddl(conn_b, schema: str, table: str) -> str:
    """The ``CREATE TABLE`` body for one table, read from the source.

    Verbatim, like every other path in this domain: ``SHOW CREATE`` already
    carries the column types, indexes, charset and table comment as the origin
    declared them, and rewriting any of that here would be the script guessing
    where the source is authoritative. The only thing added is the statement
    terminator, which is not part of the definition.

    A table that disappeared between the listing and this read is fatal rather
    than skipped: the drop for it has already been written into the plan, so a
    silent skip would leave the destination with the table gone and nothing in
    its place.
    """
    code = obj.show_create_table(conn_b, schema, table)
    if code is None:
        raise SyncError(
            f"La tabla {schema}.{table} ya no existe en el origen. "
            "Volvé a compilar el schema."
        )
    return _terminate(ddl.create_table_script(code))


def _procedure_ddl(conn_b, schema: str, procedure: str) -> str:
    """``DROP PROCEDURE IF EXISTS`` + ``CREATE PROCEDURE``, DEFINER stripped.

    ``ddl.replace_procedure_script`` is reused as is instead of calling
    ``ddl._strip_definer`` from here: it already is that function plus the drop
    line, so there is one place that knows how a procedure is rebuilt and no
    second copy of the DEFINER logic to keep in sync. The ``;`` matters here too:
    a procedure body ends in ``END``, and two of them back to back would
    otherwise be read as one very confused statement.
    """
    code = obj.show_create_procedure(conn_b, schema, procedure)
    if code is None:
        raise SyncError(
            f"El stored procedure {schema}.{procedure} ya no existe en el origen. "
            "Volvé a compilar el schema."
        )
    return _terminate(ddl.replace_procedure_script(code, schema, procedure))


def build_script(conn_b, plan: SchemaPlan) -> str:
    """Render the whole schema as one SQL script, read from the source.

    Shape: comment header, ``CREATE DATABASE`` only when the destination lacks
    the schema, then ``USE``, then the tables section (every ``DROP TABLE IF
    EXISTS`` in ``drop_order``, then every ``CREATE TABLE`` in ``create_order``),
    then the procedures section.

    ``USE`` is inside the script on purpose. ``execute_sql`` issues its own
    ``USE <schema>`` *before* the first statement, so a destination that does not
    have the schema yet fails with "Unknown database" before the script's own
    ``CREATE DATABASE`` ever runs. Carrying the ``USE`` means the caller runs the
    script with an empty ``schema_name``, the default schema sticks to the cursor
    session — the very mechanism ``execute_sql`` already relies on — and every
    statement below can stay exactly as ``SHOW CREATE`` emitted it.

    The procedures section comes after the tables, not because a procedure needs
    a table to exist (it does not, at compile time) but because structure first:
    if a ``CREATE TABLE`` fails, the procedures have not been half-updated yet,
    so the destination is a coherent "tables behind, routines as they were"
    instead of routines that point at tables that are not there.

    When the source has nothing in scope the result is the header plus the
    ``USE``: a no-op script rather than an exception. The caller still gets a
    ``script`` it can hand over, and ``CREATE DATABASE``/``USE`` are idempotent,
    so running it is harmless.
    """
    schema = plan.schema
    out: list[str] = [
        f"-- Sincronización del schema '{schema}' desde el origen (B) hacia el destino (A).",
        "-- Generado, no ejecutado: el destino cambia cuando se corra este script.",
    ]
    if plan.create_schema:
        out.append("")
        out.append(f"CREATE DATABASE IF NOT EXISTS {obj.quote_ident(schema)};")
    out.append("")
    out.append(f"USE {obj.quote_ident(schema)};")

    if plan.tables:
        out.append("")
        out.append(
            "-- Tablas: primero todos los DROP (hijas antes que padres) y después todos "
            "los CREATE (padres antes que hijas)."
        )
        for table in plan.drop_order:
            out.append(ddl.drop_table_if_exists_script(schema, table))
        for table in plan.create_order:
            out.append(_table_ddl(conn_b, schema, table))

    if plan.procedures:
        out.append("")
        out.append("-- Procedimientos: después de las tablas (estructura antes que comportamiento).")
        for procedure in plan.procedures:
            out.append(_procedure_ddl(conn_b, schema, procedure))

    return "\n".join(out) + "\n"