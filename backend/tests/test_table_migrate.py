"""The table-list plan: a date window per table, rendered half-open.

The window is the whole feature and the exclusive upper bound is the one line that
can silently lose data — ``<= '2026-01-31'`` drops every ``DATETIME`` row after
midnight of the 31st, with no error anywhere. So the boundaries are asserted as
the text that reaches MySQL rather than through a fake connection that could not
tell a correct range from a truncated one.
"""

import contextlib
from datetime import date

import pymysql
import pytest

from yappy_library.adapters.database.connection import SyncError
from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import migrate as dbmig
from yappy_library.application.database.sync import table_migrate as tm
from yappy_library.application.database.sync.migrate import (
    QueryPlan,
    SelectPlan,
    TableTarget,
)

SCHEMA = "ventas"
PEDIDOS = TableTarget(schema=SCHEMA, table="pedidos", alias="")
JANUARY = (date(2026, 1, 1), date(2026, 1, 31))


def _target(table: str) -> TableTarget:
    return TableTarget(schema=SCHEMA, table=table, alias="")


class _Conn:
    """Stands in for an open connection: the fakes only need the environment tag."""

    def __init__(self, env="dev"):
        self.env = env


B = _Conn("dev")  # origen


def _patch(monkeypatch, *, tables=(), date_columns=None):
    """Every ``db_objects`` call ``build_plan`` makes."""
    columns = date_columns or {}
    monkeypatch.setattr(obj, "list_schemas", lambda conn: [SCHEMA])
    monkeypatch.setattr(obj, "list_tables", lambda conn, schema: list(tables))
    monkeypatch.setattr(
        obj,
        "date_columns",
        lambda conn, schema, table: columns.get(f"{schema}.{table}", []),
    )


def _january(column="fecha"):
    return tm.TableFilter(column=column, date_from=JANUARY[0], date_to=JANUARY[1])


# --- the half-open window ----------------------------------------------------


def test_a_closed_window_ends_the_day_after_the_last_one():
    where = tm.render_filter(_january())

    assert where == (
        "`fecha` >= '2026-01-01 00:00:00' AND `fecha` < '2026-02-01 00:00:00'"
    )
    # The bug this shape exists to prevent: midnight of the 31st is not the 31st.
    assert "<= '2026-01-31'" not in where
    assert "'2026-01-31 23:59:59'" not in where


def test_one_day_window_is_that_whole_day():
    day = date(2026, 1, 31)
    where = tm.render_filter(tm.TableFilter(column="fecha", date_from=day, date_to=day))

    # No special case in the renderer: a same-day range is a one-day half-open one.
    assert where == (
        "`fecha` >= '2026-01-31 00:00:00' AND `fecha` < '2026-02-01 00:00:00'"
    )


def test_an_open_ended_start_renders_one_comparison():
    where = tm.render_filter(tm.TableFilter(column="fecha", date_from=date(2026, 3, 1)))

    assert where == "`fecha` >= '2026-03-01 00:00:00'"


def test_an_open_ended_end_renders_one_comparison():
    where = tm.render_filter(tm.TableFilter(column="fecha", date_to=date(2026, 2, 28)))

    assert where == "`fecha` < '2026-03-01 00:00:00'"


def test_the_last_possible_date_does_not_overflow():
    # 9999-12-31 + 1 day does not exist; nothing sits above it either, so it is
    # already the open-ended upper bound instead of an OverflowError.
    where = tm.render_filter(tm.TableFilter(column="fecha", date_to=date.max))

    assert where == ""


def test_a_column_without_dates_filters_nothing():
    assert tm.render_filter(tm.TableFilter(column="fecha")) == ""
    assert tm.render_filter(tm.TableFilter()) == ""
    assert tm.render_filter(None) == ""


# --- the SELECT of one table -------------------------------------------------


def test_an_unfiltered_table_is_a_plain_select_star():
    assert tm.select_sql(PEDIDOS, None) == "SELECT * FROM `ventas`.`pedidos`"
    assert tm.select_sql(PEDIDOS, tm.TableFilter()) == "SELECT * FROM `ventas`.`pedidos`"


def test_a_filtered_table_carries_its_window_as_a_where():
    sql = tm.select_sql(PEDIDOS, _january())

    assert sql == (
        "SELECT * FROM `ventas`.`pedidos`\n"
        "WHERE `fecha` >= '2026-01-01 00:00:00' AND `fecha` < '2026-02-01 00:00:00'"
    )


def test_a_date_and_a_datetime_column_get_the_same_boundaries(monkeypatch):
    """One mechanism for both types, which is the point of the midnight rendering.

    The renderer never sees the type; what has to hold is that a ``DATE`` column
    and a ``DATETIME`` column end up with boundaries MySQL reads the same way —
    ``2026-01-31 < '2026-02-01 00:00:00'`` is true for both, and
    ``2026-01-31 18:00:00 < '2026-02-01 00:00:00'`` is only true for the second.
    """
    _patch(
        monkeypatch,
        tables=["pedidos", "lineas"],
        date_columns={
            "ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}],
            "ventas.lineas": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "date"}],
        },
    )

    plan = tm.build_plan(B, SCHEMA, [("pedidos", _january()), ("lineas", _january())])
    by_table = {t.table: plan.select_for(t) for t in plan.tables}

    assert by_table["pedidos"] == by_table["lineas"].replace("lineas", "pedidos")
    assert "`fecha` < '2026-02-01 00:00:00'" in by_table["lineas"]


# --- validation --------------------------------------------------------------


def test_a_column_that_is_not_a_date_column_is_rejected_by_name(monkeypatch):
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={
            "ventas.pedidos": [
                {"COLUMN_NAME": "alta", "DATA_TYPE": "datetime"},
                {"COLUMN_NAME": "fecha", "DATA_TYPE": "date"},
            ]
        },
    )

    with pytest.raises(SyncError) as exc:
        tm.build_plan(B, SCHEMA, [("pedidos", _january(column="total"))])

    message = str(exc.value)
    assert "ventas.pedidos" in message
    assert "'total' no es una columna de fecha" in message
    # The valid ones are named: the client gets the fix in the same message.
    assert "alta, fecha" in message


def test_a_table_without_date_columns_says_so(monkeypatch):
    _patch(monkeypatch, tables=["pedidos"])

    with pytest.raises(SyncError) as exc:
        tm.build_plan(B, SCHEMA, [("pedidos", _january())])

    message = str(exc.value)
    assert "no tiene columnas DATE/DATETIME/TIMESTAMP" in message
    assert "Migrá la tabla completa" in message


def test_backticks_in_the_column_never_reach_a_statement(monkeypatch):
    """Nothing the client sends is interpolated unquoted, and most of it is refused.

    Two separate defences, both asserted: a column that is not a real column of the
    table is rejected outright, and one that somehow is still rendered through
    ``quote_ident``, which doubles the backtick instead of closing the identifier.
    """
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha`alta", "DATA_TYPE": "datetime"}]},
    )

    with pytest.raises(SyncError):
        tm.build_plan(B, SCHEMA, [("pedidos", _january(column="fecha` = 1 OR 1=1 -- "))])

    where = tm.render_filter(_january(column="fecha`alta"))
    assert where.startswith("`fecha``alta` >= ")
    assert " OR " not in where


def test_a_schema_the_origin_does_not_have_is_rejected(monkeypatch):
    _patch(monkeypatch, tables=[])

    with pytest.raises(SyncError) as exc:
        tm.build_plan(B, SCHEMA, [("pedidos", None)])

    assert "no existe en el origen" in str(exc.value)


def test_a_table_the_origin_does_not_have_is_rejected(monkeypatch):
    _patch(monkeypatch, tables=["pedidos"])

    with pytest.raises(SyncError) as exc:
        tm.build_plan(B, SCHEMA, [("pedidos", None), ("inventada", None)])

    assert "ventas.inventada no existe en el origen" in str(exc.value)


def test_a_blank_table_name_is_rejected(monkeypatch):
    _patch(monkeypatch, tables=["pedidos"])

    with pytest.raises(SyncError) as exc:
        tm.build_plan(B, SCHEMA, [("  ", None)])

    assert "sin nombre" in str(exc.value)


# --- the plan itself ---------------------------------------------------------


def test_the_plan_keeps_the_order_the_tables_were_chosen_in(monkeypatch):
    _patch(
        monkeypatch,
        tables=["pedidos", "lineas", "clientes"],
        date_columns={"ventas.clientes": [{"COLUMN_NAME": "alta", "DATA_TYPE": "datetime"}]},
    )

    plan = tm.build_plan(
        B,
        SCHEMA,
        [("clientes", tm.TableFilter(column="alta")), ("pedidos", None), ("lineas", None)],
    )

    assert [t.name for t in plan.tables] == [
        "ventas.clientes",
        "ventas.pedidos",
        "ventas.lineas",
    ]
    # A table with no window is not in the map at all, and that is the same as a
    # whole-table filter.
    assert set(plan.filters) == {"ventas.clientes"}
    assert plan.select_for(plan.tables[1]) == "SELECT * FROM `ventas`.`pedidos`"


def test_a_repeated_table_is_migrated_once_with_a_note(monkeypatch):
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}]},
    )

    plan = tm.build_plan(
        B,
        SCHEMA,
        [("pedidos", _january()), ("pedidos", tm.TableFilter(column="fecha"))],
    )

    assert len(plan.tables) == 1
    assert any("más de una vez" in n for n in plan.notes)


def test_the_notes_state_what_the_copy_does_to_the_destination(monkeypatch):
    _patch(monkeypatch, tables=["pedidos"])

    plan = tm.build_plan(B, SCHEMA, [("pedidos", None)])
    notes = " ".join(plan.notes)

    assert "REPLACE INTO" in notes
    assert "no borra nada" in notes
    assert "/api/compile/schema" in notes
    assert "No hay transacción" in notes


def test_a_window_without_dates_is_called_out_in_the_notes(monkeypatch):
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}]},
    )

    plan = tm.build_plan(B, SCHEMA, [("pedidos", tm.TableFilter(column="fecha"))])

    assert any("se migra completa" in n for n in plan.notes)


# --- one loop, two plan shapes -----------------------------------------------


def test_both_plan_shapes_satisfy_the_same_contract(monkeypatch):
    """The generalization is structural, so both plans are checked as such.

    ``isinstance`` against a ``Protocol`` asks "do you have these members", which
    is exactly the claim the copy loop makes: it never asks a plan what it *is*.
    """
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}]},
    )

    assert isinstance(tm.build_plan(B, SCHEMA, [("pedidos", _january())]), SelectPlan)
    assert isinstance(QueryPlan(tables=[PEDIDOS], from_clause="ventas.pedidos"), SelectPlan)


def test_a_query_plan_and_a_table_list_plan_produce_their_own_sql(monkeypatch):
    """Same contract, different answers — and neither borrows the other's text."""
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}]},
    )
    query_plan = QueryPlan(
        tables=[TableTarget(schema=SCHEMA, table="pedidos", alias="p")],
        from_clause="ventas.pedidos p",
        where_clause="p.total > 100",
    )
    list_plan = tm.build_plan(B, SCHEMA, [("pedidos", _january())])

    assert query_plan.select_for(query_plan.tables[0]).startswith("SELECT DISTINCT `p`.*")
    assert "p.total > 100" in query_plan.select_for(query_plan.tables[0])
    assert list_plan.select_for(list_plan.tables[0]) == tm.select_sql(PEDIDOS, _january())


# --- explaining a foreign key rejection --------------------------------------


def _fk_result(**overrides):
    base = dict(
        schema=SCHEMA,
        table="lineas",
        alias="",
        target_schema=SCHEMA,
        target_table="lineas",
        select_sql="SELECT * FROM `ventas`.`lineas`",
        ok=False,
        error="(1452, 'Cannot add or update a child row: a foreign key constraint fails')",
        error_errno=1452,
    )
    base.update(overrides)
    return dbmig.TableMigrationResult(**base)


def test_a_foreign_key_rejection_is_explained_instead_of_repeated():
    notes = tm.missing_parent_notes([_fk_result()])

    assert len(notes) == 1
    note = notes[0]
    assert "ventas.lineas" in note
    assert "1452" in note
    # The three things the user cannot guess from "(1452, ...)": the cause, and the
    # two ways out.
    assert "columna de fecha del padre no es la misma" in note
    assert "ampliando el rango" in note
    assert "migrando el padre completo" in note


def test_other_failures_are_not_dressed_up_as_foreign_keys():
    other = _fk_result(error="(1146, \"Table 'ventas.lineas' doesn't exist\")", error_errno=1146)

    assert tm.missing_parent_notes([other]) == []
    assert tm.missing_parent_notes([_fk_result(ok=True, error=None, error_errno=None)]) == []


def test_the_engine_keeps_the_errno_of_the_failure_it_reported(monkeypatch):
    """``migrate`` is what sees the exception, so it is what has to keep the code.

    The message alone is not enough: it *prints* 1452 but a caller matching on it
    would be parsing text the driver formats. End to end from the real pymysql
    error class, so the test breaks if that convention ever changes.
    """
    plan = tm.TableListPlan(tables=[PEDIDOS], filters={PEDIDOS.name: _january()})

    class Cfg:
        def __init__(self, tag):
            self.tag = tag
            self.env = tag

    class FakeCursor:
        def __init__(self, owner, dict_mode=False):
            self.owner = owner

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            self.owner.executed.append(sql)
            self.owner.last_rows = [{"id": 1}]

        def executemany(self, sql, seq):
            raise pymysql.err.IntegrityError(
                1452, "Cannot add or update a child row: a foreign key constraint fails"
            )

        def fetchall(self):
            return self.owner.last_rows

    class FakeConn:
        def __init__(self):
            self.executed = []
            self.last_rows = []

        def cursor(self, dict_mode=False):
            return FakeCursor(self)

    conns = {"src": FakeConn(), "dst": FakeConn()}

    @contextlib.contextmanager
    def _connect(cfg):
        yield conns[cfg.tag]

    monkeypatch.setattr(dbmig, "connect", _connect)
    monkeypatch.setattr(obj, "table_columns", lambda conn, s, t: [{"COLUMN_NAME": "id"}])
    monkeypatch.setattr(obj, "foreign_key_links", lambda conn: [])

    results = dbmig.migrate(Cfg("src"), Cfg("dst"), plan)

    assert results[0].ok is False
    assert results[0].error_errno == 1452
    assert results[0].error.startswith("(1452,")
    assert tm.missing_parent_notes(results)


def test_an_inverted_window_is_refused_instead_of_migrating_nothing(monkeypatch):
    """A window whose start is after its end can never match a row.

    Rendered literally it is ``>= '2026-02-01' AND < '2026-01-02'``, which is always
    false. The migration then reports "0 filas migradas", and that reads as "there
    was no data in that range" rather than "the two dates are the wrong way round"
    — so the user widens the range, finds nothing again, and blames the data.
    Nothing has been written when this is checked, so it is still a precondition.
    """
    _patch(
        monkeypatch,
        tables=["pedidos"],
        date_columns={"ventas.pedidos": [{"COLUMN_NAME": "fecha", "DATA_TYPE": "datetime"}]},
    )

    with pytest.raises(SyncError) as exc:
        tm.build_plan(
            B,
            SCHEMA,
            [("pedidos", tm.TableFilter(column="fecha", date_from=date(2026, 2, 1), date_to=date(2026, 1, 1)))],
        )

    detail = str(exc.value)
    assert "al revés" in detail
    assert "2026-02-01" in detail and "2026-01-01" in detail
