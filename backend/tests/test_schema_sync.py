"""Whole-schema sync: the two orderings and the script they produce.

The orderings are the feature. A schema of related tables cannot be replaced one
statement at a time, so these tests assert the *lists* directly — the helpers are
pure functions, which is what makes that possible — and then assert the shape of
the script they generate.
"""

import pytest

from yappy_library.adapters.database.connection import SyncError
from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import schema_sync as ss
from yappy_library.application.database.sync.exec import split_statements

SCHEMA = "yappy"


class _Conn:
    """Stands in for an open connection: the fakes only need to tell the two
    environments apart."""

    def __init__(self, env):
        self.env = env


B = _Conn("dev")  # origen
A = _Conn("qa")  # destino


def _patch(
    monkeypatch,
    *,
    schemas=None,
    tables=None,
    procedures=None,
    links=None,
    create_table=None,
    create_procedure=None,
):
    """Every ``db_objects`` call the module makes, keyed by environment."""
    schemas = schemas or {}
    tables = tables or {}
    procedures = procedures or {}
    links = links or {}
    create_table = create_table or {}
    create_procedure = create_procedure or {}

    monkeypatch.setattr(obj, "list_schemas", lambda conn: schemas.get(conn.env, []))
    monkeypatch.setattr(obj, "list_tables", lambda conn, schema: tables.get(conn.env, []))
    monkeypatch.setattr(
        obj, "list_procedures", lambda conn, schema: procedures.get(conn.env, [])
    )
    monkeypatch.setattr(obj, "foreign_key_links", lambda conn: links.get(conn.env, []))
    monkeypatch.setattr(
        obj, "show_create_table", lambda conn, schema, name: create_table.get((conn.env, name))
    )
    monkeypatch.setattr(
        obj,
        "show_create_procedure",
        lambda conn, schema, name: create_procedure.get((conn.env, name)),
    )


def _full_world(monkeypatch, **kwargs):
    """The common case: the schema exists on both sides."""
    _patch(
        monkeypatch,
        schemas={B.env: [SCHEMA], A.env: [SCHEMA]},
        **kwargs,
    )


# --- plan: source and destination must both be readable ----------------------


def test_plan_rejects_a_schema_that_is_not_in_the_source(monkeypatch):
    _patch(monkeypatch, schemas={B.env: ["otra"], A.env: [SCHEMA]})

    with pytest.raises(SyncError) as exc:
        ss.plan(B, A, SCHEMA, True, True)

    assert f"'{SCHEMA}'" in str(exc.value)
    assert "origen" in str(exc.value)


def test_plan_flags_a_schema_the_destination_does_not_have(monkeypatch):
    _patch(monkeypatch, schemas={B.env: [SCHEMA], A.env: []})
    p = ss.plan(B, A, SCHEMA, True, True)
    assert p.create_schema is True

    _full_world(monkeypatch)
    assert ss.plan(B, A, SCHEMA, True, True).create_schema is False


def test_plan_only_lists_what_was_asked_for(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        procedures={B.env: ["sp_calc"]},
    )

    only_tables = ss.plan(B, A, SCHEMA, True, False)
    assert only_tables.tables == ["orders"]
    assert only_tables.procedures == []

    only_procedures = ss.plan(B, A, SCHEMA, False, True)
    assert only_procedures.tables == []
    assert only_procedures.procedures == ["sp_calc"]


# --- plan: drop order follows the DESTINATION graph --------------------------


def test_drop_order_drops_the_child_before_the_parent(monkeypatch):
    """`lines` references `orders` in the destination.

    Those are the constraints that are alive when the script runs, so `lines` goes
    first: dropping `orders` first is rejected with 3730.
    """
    _full_world(
        monkeypatch,
        tables={B.env: ["lines", "orders"], A.env: ["lines", "orders"]},
        links={A.env: [(SCHEMA, "lines", SCHEMA, "orders")]},
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    assert p.drop_order == ["lines", "orders"]
    # The source has no such edge, so the create order has no reason to differ.
    assert p.create_order == ["lines", "orders"]


# --- plan: create order follows the SOURCE graph -----------------------------


def test_create_order_creates_the_parent_before_the_child(monkeypatch):
    """Every incoming ``CREATE TABLE`` declares the origin's constraints, so a
    child created before its parent does not exist fails on the statement."""
    _full_world(
        monkeypatch,
        tables={B.env: ["lines", "orders"], A.env: ["lines", "orders"]},
        links={B.env: [(SCHEMA, "lines", SCHEMA, "orders")]},
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    assert p.create_order == ["orders", "lines"]
    # And the destination has no edge, so nothing reorders the drops.
    assert p.drop_order == ["lines", "orders"]


# --- plan: the two graphs are genuinely different objects -------------------


def test_the_two_orders_come_from_different_graphs(monkeypatch):
    """Destination says ``orders`` is a child of ``lines``; the source says it is a
    child of ``customers``.

    Reading one graph for both orders would get exactly one of the two lists
    wrong: the same edge direction cannot both drop a parent before its child and
    create it after.
    """
    _full_world(
        monkeypatch,
        tables={
            B.env: ["customers", "lines", "orders"],
            A.env: ["customers", "lines", "orders"],
        },
        links={
            B.env: [(SCHEMA, "orders", SCHEMA, "customers")],
            A.env: [(SCHEMA, "orders", SCHEMA, "lines")],
        },
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    # `lines` holds the destination's `orders`, so it goes last among the drops.
    assert p.drop_order == ["customers", "orders", "lines"]
    # `customers` holds the source's `orders`, so it goes before it among the creates.
    assert p.create_order == ["customers", "lines", "orders"]
    assert p.drop_order != p.create_order


# --- plan: what is out of scope cannot constrain the order ------------------


def test_a_table_whose_parent_is_out_of_scope_is_not_blocked(monkeypatch):
    """`orders` references `legacy`, which the script does not touch.

    `legacy` already exists in the destination and keeps existing, so it says
    nothing about when `orders` may be dropped or created.
    """
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["legacy", "orders"]},
        links={
            B.env: [(SCHEMA, "orders", SCHEMA, "legacy")],
            A.env: [(SCHEMA, "orders", SCHEMA, "legacy")],
        },
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    assert p.drop_order == ["orders"]
    assert p.create_order == ["orders"]


def test_left_alone_is_the_destination_minus_the_scope_and_is_never_dropped(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["legacy", "orders"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
    )

    p = ss.plan(B, A, SCHEMA, True, False)
    assert p.left_alone == ["legacy"]

    script = ss.build_script(B, p)
    assert "legacy" not in script


def test_an_explicit_list_narrows_the_scope_to_exactly_what_it_names(monkeypatch):
    """La página de sincronizar muestra una tabla con una casilla por objeto: lo
    que llega es la lista de lo que el usuario dejó marcado."""
    _full_world(
        monkeypatch,
        tables={B.env: ["orders", "lines", "config"], A.env: ["orders", "lines", "config"]},
        procedures={B.env: ["sp_calc", "sp_sync"], A.env: ["sp_calc", "sp_sync"]},
    )

    p = ss.plan(
        B, A, SCHEMA, True, True, only_tables=["lines"], only_procedures=["sp_calc"]
    )

    assert p.tables == ["lines"]
    assert p.procedures == ["sp_calc"]
    # Y los dos órdenes son permutaciones de esa misma lista, no de la original.
    assert sorted(p.drop_order) == ["lines"]
    assert sorted(p.create_order) == ["lines"]


def test_an_explicit_list_is_not_widened_by_the_flags(monkeypatch):
    """`include_tables=True` es el default del request. Si se aplicara después de
    la lista, volvería a compilar todo lo que el usuario recién desmarcó."""
    _full_world(
        monkeypatch,
        tables={B.env: ["orders", "lines"], A.env: ["orders", "lines"]},
        procedures={B.env: ["sp_calc"], A.env: ["sp_calc"]},
    )

    p = ss.plan(B, A, SCHEMA, True, True, only_tables=["orders"], only_procedures=[])

    assert p.tables == ["orders"]
    assert p.procedures == []


def test_an_explicit_empty_list_is_nothing_rather_than_everything(monkeypatch):
    """Vaciar la tabla de casillas es una decisión, no un request sin alcance: si
    leyera como "no se envió lista", compilaría el esquema entero que el usuario
    acababa de dejar vacío."""
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        procedures={B.env: ["sp_calc"], A.env: ["sp_calc"]},
    )

    p = ss.plan(B, A, SCHEMA, True, True, only_tables=[], only_procedures=[])

    assert p.tables == []
    assert p.procedures == []


def test_a_list_that_names_something_the_origin_does_not_have_is_rejected(monkeypatch):
    """El cliente está desactualizado, o el nombre está mal. Decirlo ahora evita un
    script con un hueco adentro que nadie nota hasta que falta una tabla."""
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        procedures={B.env: ["sp_calc"], A.env: ["sp_calc"]},
    )

    with pytest.raises(SyncError) as exc:
        ss.plan(B, A, SCHEMA, True, True, only_tables=["orders", "typo"], only_procedures=None)

    message = str(exc.value)
    assert "typo" in message
    # Y dice cuál era la lista real, para que se sepa qué volver a marcar.
    assert "orders" in message


def test_only_one_kind_can_be_narrowed(monkeypatch):
    """Las tablas y los procedures se eligen por separado: un run de sólo tablas
    con sus procedures intactos es un request normal."""
    _full_world(
        monkeypatch,
        tables={B.env: ["orders", "lines"], A.env: ["orders", "lines"]},
        procedures={B.env: ["sp_calc", "sp_sync"], A.env: ["sp_calc", "sp_sync"]},
    )

    p = ss.plan(B, A, SCHEMA, True, True, only_tables=["orders"], only_procedures=None)

    assert p.tables == ["orders"]
    # `only_procedures=None` = "no lo restringí": todos los procedures.
    assert p.procedures == ["sp_calc", "sp_sync"]


def test_a_narrowed_list_still_reports_what_the_destination_keeps(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders", "lines"], A.env: ["orders", "lines", "legacy"]},
    )

    p = ss.plan(B, A, SCHEMA, True, True, only_tables=["orders"], only_procedures=[])

    # `lines` y `legacy` quedan como están: `lines` porque el usuario no la marcó,
    # `legacy` porque el origen no la tiene.
    assert p.left_alone == ["legacy", "lines"]


def test_left_alone_is_empty_when_tables_are_excluded(monkeypatch):
    """On a procedures-only run nothing is reported about tables, and that is the point.

    Every destination table is technically "not in scope" here, so a naive
    destination-minus-scope would report all of them — and the note built from
    that list says the *origin* does not have them, which is false. The user did
    not ask for tables; that is not the same thing as the origin lacking them.
    """
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["legacy", "orders"]},
        procedures={B.env: ["sp_calc"], A.env: []},
        create_procedure={
            (B.env, "sp_calc"): "CREATE PROCEDURE `sp_calc`() BEGIN SELECT 1; END"
        },
    )

    p = ss.plan(B, A, SCHEMA, False, True)

    assert p.tables == []
    assert p.left_alone == []
    # And nothing claims the origin is missing tables.
    assert not any("el origen no tiene" in n for n in p.notes)
    assert "DROP TABLE" not in ss.build_script(B, p)


def test_a_foreign_key_from_another_schema_is_reported_not_reordered(monkeypatch):
    """Another schema's table pointing at ours is not ours to reorder around.

    The script cannot move it, so the only honest thing to do is name it: the
    rows it holds will lose their parent when the table is replaced.
    """
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        links={A.env: [("otro", "auditoria", SCHEMA, "orders")]},
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    assert p.drop_order == ["orders"]
    assert any("otro" in note for note in p.notes)


# --- the ordering helpers, on their own -------------------------------------


def test_ordered_emits_everything_when_nothing_blocks_anything():
    assert ss._ordered(["a", "b", "c"], {}) == ["a", "b", "c"]


def test_ordered_respects_a_chain():
    blockers = {"c": {"b"}, "b": {"a"}}
    assert ss._ordered(["a", "b", "c"], blockers) == ["a", "b", "c"]
    assert ss._ordered(["c", "b", "a"], blockers) == ["a", "b", "c"]


def test_ordered_falls_back_to_the_input_order_on_a_cycle():
    """MySQL forbids cycles, so this is a corrupt graph rather than an exotic
    schema. Looping forever would be worse than being wrong in a known way, and
    this is the same fallback `migrate.order_by_dependencies` uses."""
    blockers = {"a": {"b"}, "b": {"a"}}
    assert ss._ordered(["a", "b"], blockers) == ["a", "b"]


def test_plan_falls_back_to_the_input_order_on_a_destination_cycle(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["lines", "orders"], A.env: ["lines", "orders"]},
        links={
            A.env: [
                (SCHEMA, "lines", SCHEMA, "orders"),
                (SCHEMA, "orders", SCHEMA, "lines"),
            ]
        },
    )

    p = ss.plan(B, A, SCHEMA, True, False)

    assert p.drop_order == p.tables == ["lines", "orders"]


def test_in_scope_children_drops_edges_that_leave_the_scope():
    links = [
        (SCHEMA, "lines", SCHEMA, "orders"),  # both in scope
        (SCHEMA, "orders", SCHEMA, "legacy"),  # parent out of scope
        ("otro", "auditoria", SCHEMA, "orders"),  # other schema
        (SCHEMA, "lines", "otro", "orders"),  # parent in another schema
    ]
    assert ss._in_scope_children(links, SCHEMA, ["lines", "orders"]) == {"orders": {"lines"}}


# --- build_script ------------------------------------------------------------


def test_script_creates_the_database_when_the_destination_lacks_the_schema(monkeypatch):
    _patch(
        monkeypatch,
        schemas={B.env: [SCHEMA], A.env: []},
        tables={B.env: ["orders"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    assert f"CREATE DATABASE IF NOT EXISTS `{SCHEMA}`;" in script


def test_script_does_not_create_a_database_the_destination_already_has(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    assert "CREATE DATABASE" not in script


def test_script_always_uses_the_schema(monkeypatch):
    """Even when it has to create it.

    `execute_sql` issues its own `USE <schema>` before the first statement, which
    is exactly what fails on a destination that does not have the schema yet. The
    `USE` lives in the script so the caller can run it with an empty
    `schema_name` and let the default schema stick to the session.
    """
    _patch(
        monkeypatch,
        schemas={B.env: [SCHEMA], A.env: []},
        tables={B.env: ["orders"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))
    assert f"USE `{SCHEMA}`;" in script
    # ...and it comes right after the CREATE DATABASE, before any DDL.
    assert script.index(f"USE `{SCHEMA}`;") < script.index("CREATE TABLE")
    assert script.index(f"USE `{SCHEMA}`;") > script.index("CREATE DATABASE IF NOT EXISTS")


def test_create_table_bodies_are_verbatim_from_show_create(monkeypatch):
    ddl_b = (
        "CREATE TABLE `orders` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `note` varchar(64) DEFAULT NULL COMMENT 'n',\n"
        "  PRIMARY KEY (`id`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='pedidos'"
    )
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        create_table={(B.env, "orders"): ddl_b},
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    # Byte for byte: not re-indented, not qualified with the schema, not commented.
    assert ddl_b in script
    assert script.count("CREATE TABLE") == 1


def test_tables_section_drops_everything_before_creating_anything(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["customers", "orders"], A.env: ["customers", "orders"]},
        links={
            B.env: [(SCHEMA, "orders", SCHEMA, "customers")],
            A.env: [(SCHEMA, "orders", SCHEMA, "customers")],
        },
        create_table={
            (B.env, "customers"): "CREATE TABLE `customers` (`id` int)",
            (B.env, "orders"): "CREATE TABLE `orders` (`id` int)",
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    assert script.index("DROP TABLE IF EXISTS `yappy`.`orders`;") < script.index(
        "DROP TABLE IF EXISTS `yappy`.`customers`;"
    )
    assert script.index("DROP TABLE IF EXISTS `yappy`.`customers`;") < script.index(
        "CREATE TABLE `customers`"
    )


def test_procedures_come_after_the_tables_with_the_definer_stripped(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["orders"]},
        procedures={B.env: ["sp_calc", "sp_other"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
        create_procedure={
            (
                B.env,
                "sp_calc",
            ): "CREATE DEFINER=`admin`@`%` PROCEDURE `sp_calc`()\nBEGIN\n  SELECT 1;\nEND",
            (B.env, "sp_other"): "CREATE PROCEDURE `sp_other`()\nBEGIN\n  SELECT 2;\nEND",
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, True))

    # Structure before behaviour: the tables are settled first.
    assert script.index("CREATE TABLE `orders`") < script.index("DROP PROCEDURE IF EXISTS")
    assert script.index("DROP PROCEDURE IF EXISTS `yappy`.`sp_calc`;") < script.index(
        "DROP PROCEDURE IF EXISTS `yappy`.`sp_other`;"
    )
    # The DEFINER is gone: the procedure belongs to whoever runs the script.
    assert "DEFINER" not in script
    assert "CREATE PROCEDURE `sp_calc`()" in script
    # The body survives untouched, with its own line structure.
    assert "CREATE PROCEDURE `sp_calc`()\nBEGIN\n  SELECT 1;\nEND" in script


def test_excluding_procedures_drops_the_whole_section(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"]},
        procedures={B.env: ["sp_calc"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
        create_procedure={
            (B.env, "sp_calc"): "CREATE PROCEDURE `sp_calc`() BEGIN SELECT 1; END"
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    assert "PROCEDURE" not in script
    assert "sp_calc" not in script


def test_excluding_tables_drops_the_whole_section(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"]},
        procedures={B.env: ["sp_calc"]},
        create_procedure={
            (B.env, "sp_calc"): "CREATE PROCEDURE `sp_calc`() BEGIN SELECT 1; END"
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, False, True))

    assert "DROP TABLE" not in script
    assert "CREATE TABLE" not in script
    assert "DROP PROCEDURE IF EXISTS `yappy`.`sp_calc`;" in script


def test_a_schema_with_nothing_in_scope_is_a_no_op_script(monkeypatch):
    """A header and a `USE`, not an exception: the caller still gets a script it
    can hand over, and `CREATE DATABASE`/`USE` are idempotent."""
    _full_world(monkeypatch, tables={B.env: [], A.env: []}, procedures={B.env: []})

    p = ss.plan(B, A, SCHEMA, True, True)
    script = ss.build_script(B, p)

    assert p.tables == [] and p.procedures == []
    assert f"USE `{SCHEMA}`;" in script
    assert "DROP" not in script
    assert "CREATE" not in script
    assert any("no tiene tablas" in note for note in p.notes)


def test_notes_warn_about_the_rows_and_about_there_being_no_transaction(monkeypatch):
    _full_world(
        monkeypatch,
        tables={B.env: ["orders"], A.env: ["legacy", "orders"]},
        create_table={(B.env, "orders"): "CREATE TABLE `orders` (`id` int)"},
    )

    notes = ss.plan(B, A, SCHEMA, True, False).notes

    assert any("filas" in note for note in notes)
    assert any("legacy" in note for note in notes)
    assert any("autocommit" in note for note in notes)


def test_an_object_that_vanished_is_an_error_not_a_silent_skip(monkeypatch):
    """The drop for it is already in the plan. A silent skip would leave the
    destination with the table gone and nothing in its place."""
    _full_world(monkeypatch, tables={B.env: ["orders"], A.env: ["orders"]})

    with pytest.raises(SyncError) as exc:
        ss.build_script(B, ss.plan(B, A, SCHEMA, True, False))

    assert f"yappy.orders" in str(exc.value)


# --- the shape of a real script ----------------------------------------------


def test_a_two_table_schema_produces_one_readable_script(monkeypatch):
    """`lines` references `orders` on both sides: the same edge, walked in opposite
    directions, is the whole story of the two sections."""
    _full_world(
        monkeypatch,
        tables={B.env: ["lines", "orders"], A.env: ["lines", "orders"]},
        procedures={B.env: ["sp_lines"]},
        links={
            B.env: [(SCHEMA, "lines", SCHEMA, "orders")],
            A.env: [(SCHEMA, "lines", SCHEMA, "orders")],
        },
        create_table={
            (B.env, "orders"): "CREATE TABLE `orders` (`id` int NOT NULL, PRIMARY KEY (`id`))",
            (
                B.env,
                "lines",
            ): (
                "CREATE TABLE `lines` (\n"
                "  `id` int NOT NULL,\n"
                "  `order_id` int NOT NULL,\n"
                "  PRIMARY KEY (`id`),\n"
                "  KEY `fk_lines_orders` (`order_id`),\n"
                "  CONSTRAINT `fk_lines_orders` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)\n"
                ") ENGINE=InnoDB"
            ),
        },
        create_procedure={
            (
                B.env,
                "sp_lines",
            ): "CREATE DEFINER=`root`@`localhost` PROCEDURE `sp_lines`()\nBEGIN\n  SELECT 1;\nEND",
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, True))

    assert script == (
        f"-- Sincronización del schema '{SCHEMA}' desde el origen (B) hacia el destino (A).\n"
        "-- Generado, no ejecutado: el destino cambia cuando se corra este script.\n"
        "\n"
        f"USE `{SCHEMA}`;\n"
        "\n"
        "-- Tablas: primero todos los DROP (hijas antes que padres) y después todos "
        "los CREATE (padres antes que hijas).\n"
        "DROP TABLE IF EXISTS `yappy`.`lines`;\n"
        "DROP TABLE IF EXISTS `yappy`.`orders`;\n"
        "CREATE TABLE `orders` (`id` int NOT NULL, PRIMARY KEY (`id`));\n"
        "CREATE TABLE `lines` (\n"
        "  `id` int NOT NULL,\n"
        "  `order_id` int NOT NULL,\n"
        "  PRIMARY KEY (`id`),\n"
        "  KEY `fk_lines_orders` (`order_id`),\n"
        "  CONSTRAINT `fk_lines_orders` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`)\n"
        ") ENGINE=InnoDB;\n"
        "\n"
        "-- Procedimientos: después de las tablas (estructura antes que comportamiento).\n"
        "DROP PROCEDURE IF EXISTS `yappy`.`sp_lines`;\n"
        "CREATE PROCEDURE `sp_lines`()\n"
        "BEGIN\n"
        "  SELECT 1;\n"
        "END;\n"
    )


def test_the_script_splits_into_exactly_one_statement_per_object(monkeypatch):
    """Every statement in the script is terminated, so the splitter sees one each.

    ``SHOW CREATE`` does not emit a trailing ``;`` — it does not need one, because
    it is the whole answer on its own. The single-object compile gets away with
    that because its ``CREATE`` is always last in the script. This script is a
    sequence, and ``exec.split_statements`` splits on ``;``: two unterminated
    ``CREATE TABLE`` bodies reach MySQL glued together and come back as a 1064
    that points at the *second* table, which reads like a bug in that table.

    Asserting the script's text is not enough — it looked fine. It has to survive
    the same splitter the executor uses.
    """
    _full_world(
        monkeypatch,
        tables={B.env: ["customers", "orders"], A.env: ["customers", "orders"]},
        procedures={B.env: ["sp_a", "sp_b"], A.env: []},
        create_table={
            (B.env, "customers"): "CREATE TABLE `customers` (`id` int NOT NULL)",
            (B.env, "orders"): "CREATE TABLE `orders` (`id` int NOT NULL)",
        },
        create_procedure={
            (B.env, "sp_a"): "CREATE PROCEDURE `sp_a`() BEGIN SELECT 1; END",
            (B.env, "sp_b"): "CREATE PROCEDURE `sp_b`() BEGIN SELECT 2; END",
        },
    )

    script = ss.build_script(B, ss.plan(B, A, SCHEMA, True, True))
    statements = split_statements(script)

    # USE + 2 drops + 2 creates + 2 procedure drops + 2 procedure creates.
    assert len(statements) == 9
    # Nothing glued: no statement mentions two objects.
    for stmt in statements:
        assert stmt.count("CREATE TABLE") <= 1
        assert stmt.count("CREATE PROCEDURE") <= 1
    # And each one really is one of ours, not a fragment.
    assert any(s.startswith("CREATE TABLE `customers`") for s in statements)
    assert any(s.startswith("CREATE PROCEDURE `sp_b`()") for s in statements)