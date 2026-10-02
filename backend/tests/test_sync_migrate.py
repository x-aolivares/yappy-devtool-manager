"""Parsing of a query into per-table migrations (Region Sync "Migrar info")."""

import contextlib

import pytest

from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import migrate as dbmig
from yappy_library.application.database.sync import migrate as m
from yappy_library.application.database.sync.migrate import MigrationError, parse_select
from yappy_library.application.database.sync.query import QueryError


EXAMPLE = """
SELECT * FROM schema_abc.table_abc abc, schema_zxc.zxc zxc
WHERE zxc.abc_id = abc.abc_id
  AND zxc.zxc_status = 'COMPLETED'
  AND abc.abc_type = 'M2P'
  AND abc.abc_cutoff_date = '2026-10-01'
"""


def _names(plan):
    return [t.name for t in plan.tables]


def test_finds_every_table_in_the_query():
    plan = parse_select(EXAMPLE)
    assert _names(plan) == ["schema_abc.table_abc", "schema_zxc.zxc"]
    assert [t.alias for t in plan.tables] == ["abc", "zxc"]


def _select_for(plan, alias):
    target = next(t for t in plan.tables if t.alias == alias)
    return dbmig.table_select(plan, target)


def test_each_table_gets_its_own_projected_select():
    plan = parse_select(EXAMPLE)

    abc = _select_for(plan, "abc")
    assert abc.startswith("SELECT DISTINCT `abc`.*")
    # Every table reuses the original joins AND the original filters.
    assert "schema_abc.table_abc abc" in abc
    assert "schema_zxc.zxc zxc" in abc
    assert "zxc.abc_id = abc.abc_id" in abc
    assert "abc.abc_type = 'M2P'" in abc
    assert "abc.abc_cutoff_date = '2026-10-01'" in abc

    zxc = _select_for(plan, "zxc")
    assert zxc.startswith("SELECT DISTINCT `zxc`.*")
    assert "zxc.zxc_status = 'COMPLETED'" in zxc


def test_default_schema_applies_to_unqualified_tables():
    plan = parse_select("SELECT * FROM orders o WHERE o.status = 'X'", default_schema="shop")
    assert _names(plan) == ["shop.orders"]
    assert plan.tables[0].alias == "o"


def test_qualified_tables_ignore_the_default_schema():
    plan = parse_select("SELECT * FROM a.x, b.y", default_schema="unused")
    assert _names(plan) == ["a.x", "b.y"]


def test_unqualified_table_without_default_schema_is_rejected():
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT * FROM orders")
    assert "no tiene schema" in str(exc.value)


def test_explicit_join_syntax():
    plan = parse_select(
        "SELECT * FROM shop.orders o "
        "INNER JOIN shop.lines l ON l.order_id = o.id "
        "LEFT OUTER JOIN shop.ship s ON s.order_id = o.id "
        "WHERE o.total > 10"
    )
    assert _names(plan) == ["shop.orders", "shop.lines", "shop.ship"]


def test_as_keyword_for_alias():
    plan = parse_select("SELECT * FROM shop.orders AS o, shop.lines l")
    assert [t.alias for t in plan.tables] == ["o", "l"]


def test_table_without_alias_projects_by_qualified_name():
    plan = parse_select("SELECT * FROM shop.orders WHERE id = 1")
    assert plan.tables[0].alias == ""
    assert m.table_select(plan, plan.tables[0]).startswith(
        "SELECT DISTINCT `shop`.`orders`.*"
    )


def test_backtick_quoted_names_are_unquoted():
    plan = parse_select("SELECT * FROM `my-schema`.`orders` `o` WHERE `o`.id = 1")
    assert _names(plan) == ["my-schema.orders"]
    assert plan.tables[0].alias == "o"


def test_keyword_inside_a_string_is_not_a_clause():
    plan = parse_select("SELECT * FROM shop.orders o WHERE o.note = 'from where join'")
    assert _names(plan) == ["shop.orders"]
    assert "from where join" in plan.where_clause


def test_subquery_in_where_is_preserved_verbatim():
    plan = parse_select(
        "SELECT * FROM shop.orders o WHERE o.id IN (SELECT id FROM shop.archive WHERE x = 1)"
    )
    assert _names(plan) == ["shop.orders"]
    assert "shop.archive" in plan.where_clause


def test_subquery_in_from_is_rejected():
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT * FROM (SELECT 1 AS x) t")
    assert "tabla derivada" in str(exc.value)


def test_group_by_is_rejected_with_an_explanation():
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT o.id, SUM(l.q) FROM o JOIN l ON 1=1 GROUP BY o.id")
    assert "GROUP BY" in str(exc.value)


def test_union_is_rejected():
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT * FROM a UNION SELECT * FROM b")
    assert "UNION" in str(exc.value)


def test_order_by_and_limit_are_dropped_with_a_note():
    plan = parse_select("SELECT * FROM shop.orders o ORDER BY o.id LIMIT 10")
    assert "ORDER BY" not in plan.from_clause
    assert "LIMIT" not in plan.where_clause
    assert any("ORDER BY" in n for n in plan.notes)


def test_same_table_twice_is_migrated_once_with_a_note():
    plan = parse_select("SELECT * FROM shop.orders a, shop.orders b WHERE a.id = b.parent")
    assert _names(plan) == ["shop.orders"]
    assert any("más de una vez" in n for n in plan.notes)


def test_dual_is_ignored():
    # SELECT 1 FROM DUAL references no real table, so there is nothing to migrate.
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT 1 FROM DUAL")
    assert "no referencia ninguna tabla" in str(exc.value)


def test_no_from_is_rejected():
    with pytest.raises(MigrationError) as exc:
        parse_select("SELECT 1")
    assert "no tiene FROM" in str(exc.value)


def test_only_read_statements_are_accepted():
    with pytest.raises(QueryError):
        parse_select("DELETE FROM shop.orders")


# --- migrate() --------------------------------------------------------------


class FakeCursor:
    def __init__(self, owner, dict_mode=False):
        self.owner = owner
        self.dict_mode = dict_mode
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.owner.executed.append(sql)
        for key, value in self.owner.rows.items():
            if key in sql:
                self.owner.last_rows = value
                return
        self.owner.last_rows = []

    def executemany(self, sql, seq):
        self.owner.executed.append(sql)
        self.owner.batches.append(list(seq))
        self.owner.last_rows = []

    def fetchall(self):
        return self.owner.last_rows

    def fetchone(self):
        return (len(self.owner.last_rows),)

    def close(self):
        pass


class FakeConn:
    def __init__(self, tables=None, rows=None, fail_on=None):
        self.tables = tables if tables is not None else {}
        self.rows = rows if rows is not None else {}
        self.fail_on = fail_on
        self.executed = []
        self.batches = []
        self.last_rows = []

    def cursor(self, dict_mode=False):
        return FakeCursor(self, dict_mode=dict_mode)

    def columns_for(self, schema, table):
        return self.tables.get(f"{schema}.{table}", [])


@pytest.fixture
def fake_connections(monkeypatch):
    """Route every connect() to one fake connection, keyed by config identity."""

    class Cfg:
        def __init__(self, tag):
            self.tag = tag
            # El Config real lo tiene, y el mensaje de error por tabla lo nombra.
            self.env = tag

    state = {}

    @contextlib.contextmanager
    def _connect(cfg):
        conn = state[cfg.tag]
        yield conn

    monkeypatch.setattr(m, "connect", _connect)
    monkeypatch.setattr(obj, "table_columns", lambda conn, s, t: conn.columns_for(s, t))
    monkeypatch.setattr(obj, "foreign_key_links", lambda conn: [])

    def make(src=None, dst=None):
        state["src"] = src or FakeConn()
        state["dst"] = dst or FakeConn()
        return Cfg("src"), Cfg("dst")

    return make


def test_migrate_copies_each_table_with_replace_into(fake_connections):
    src = FakeConn(
        tables={"shop.orders": [{"COLUMN_NAME": "id"}, {"COLUMN_NAME": "total"}]},
        rows={
            "SELECT DISTINCT `o`.*": [{"id": 1, "total": 10}, {"id": 2, "total": 20}],
            "SELECT DISTINCT `l`.*": [{"id": 7}],
        },
    )
    dst = FakeConn(
        tables={
            "shop.orders": [{"COLUMN_NAME": "id"}, {"COLUMN_NAME": "total"}],
            "shop.lines": [{"COLUMN_NAME": "id"}],
        }
    )
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select(
        "SELECT * FROM shop.orders o, shop.lines l "
        "WHERE l.order_id = o.id AND o.total > 5"
    )
    results = m.migrate(cfg_b, cfg_a, plan)

    by_table = {f"{r.schema}.{r.table}": r for r in results}
    assert set(by_table) == {"shop.orders", "shop.lines"}
    assert all(r.ok for r in results)
    assert by_table["shop.orders"].row_count == 2
    assert by_table["shop.orders"].replaced == 2
    assert by_table["shop.lines"].row_count == 1

    writes = [s for s in dst.executed if s.startswith("REPLACE INTO")]
    assert len(writes) == 2
    assert "`shop`.`orders` (`id`, `total`)" in writes[0]
    assert dst.batches[0] == [(1, 10), (2, 20)]


def test_migrate_dry_run_reads_but_never_writes(fake_connections):
    src = FakeConn(
        tables={"shop.orders": [{"COLUMN_NAME": "id"}]},
        rows={"SELECT DISTINCT `o`.*": [{"id": 1}, {"id": 2}]},
    )
    dst = FakeConn(tables={"shop.orders": [{"COLUMN_NAME": "id"}]})
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select("SELECT * FROM shop.orders o")
    results = m.migrate(cfg_b, cfg_a, plan, dry_run=True)

    assert results[0].row_count == 2
    assert results[0].replaced == 0
    assert not [s for s in dst.executed if s.startswith("REPLACE INTO")]


def test_missing_destination_table_fails_that_table_only(fake_connections):
    src = FakeConn(
        tables={"shop.orders": [{"COLUMN_NAME": "id"}]},
        rows={"SELECT DISTINCT `o`.*": [{"id": 1}]},
    )
    dst = FakeConn(tables={})  # nothing exists in the destination
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select("SELECT * FROM shop.orders o")
    results = m.migrate(cfg_b, cfg_a, plan)

    assert len(results) == 1
    assert results[0].ok is False
    assert "no existe en el ambiente destino" in results[0].error


def test_columns_absent_in_the_destination_are_skipped_and_reported(fake_connections):
    src = FakeConn(
        tables={"shop.orders": [{"COLUMN_NAME": "id"}]},
        rows={"SELECT DISTINCT `o`.*": [{"id": 1, "new_col": "x"}]},
    )
    dst = FakeConn(tables={"shop.orders": [{"COLUMN_NAME": "id"}]})
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select("SELECT * FROM shop.orders o")
    results = m.migrate(cfg_b, cfg_a, plan)

    assert results[0].ok is True
    assert results[0].skipped_columns == ["new_col"]
    write = [s for s in dst.executed if s.startswith("REPLACE INTO")][0]
    assert "`id`)" in write
    assert "new_col" not in write


def test_a_failing_table_does_not_stop_the_others(fake_connections, monkeypatch):
    src = FakeConn(
        tables={
            "shop.orders": [{"COLUMN_NAME": "id"}],
            "shop.lines": [{"COLUMN_NAME": "id"}],
        },
        rows={
            "SELECT DISTINCT `o`.*": [{"id": 1}],
            "SELECT DISTINCT `l`.*": [{"id": 2}],
        },
    )
    dst = FakeConn(
        tables={
            "shop.orders": [{"COLUMN_NAME": "id"}],
            "shop.lines": [{"COLUMN_NAME": "id"}],
        }
    )
    cfg_b, cfg_a = fake_connections(src, dst)

    real_executemany = FakeCursor.executemany

    def boom(self, sql, seq):
        if "`shop`.`orders`" in sql:
            raise RuntimeError("destino rechaza la escritura")
        return real_executemany(self, sql, seq)

    monkeypatch.setattr(FakeCursor, "executemany", boom)

    plan = parse_select("SELECT * FROM shop.orders o, shop.lines l")
    results = {f"{r.schema}.{r.table}": r for r in m.migrate(cfg_b, cfg_a, plan)}

    assert results["shop.orders"].ok is False
    assert "rechaza" in results["shop.orders"].error
    assert results["shop.lines"].ok is True


def test_order_by_dependencies_writes_parents_first(fake_connections, monkeypatch):
    src = FakeConn(
        tables={
            "shop.lines": [{"COLUMN_NAME": "id"}],
            "shop.orders": [{"COLUMN_NAME": "id"}],
        },
        rows={
            "SELECT DISTINCT `l`.*": [{"id": 1}],
            "SELECT DISTINCT `o`.*": [{"id": 2}],
        },
    )
    dst = FakeConn(
        tables={
            "shop.lines": [{"COLUMN_NAME": "id"}],
            "shop.orders": [{"COLUMN_NAME": "id"}],
        }
    )
    cfg_b, cfg_a = fake_connections(src, dst)
    # lines references orders -> orders must be written first.
    monkeypatch.setattr(
        obj,
        "foreign_key_links",
        lambda conn: [("shop", "lines", "shop", "orders")],
    )

    plan = parse_select("SELECT * FROM shop.lines l, shop.orders o")
    results = m.migrate(cfg_b, cfg_a, plan)

    written = [s for s in dst.executed if s.startswith("REPLACE INTO")]
    assert [s.split("`")[3] for s in written] == ["orders", "lines"]
    # The response still lists the tables in the query's own order.
    assert [f"{r.schema}.{r.table}" for r in results] == ["shop.lines", "shop.orders"]


def test_rows_are_written_in_batches(fake_connections):
    src = FakeConn(
        tables={"shop.orders": [{"COLUMN_NAME": "id"}]},
        rows={"SELECT DISTINCT `o`.*": [{"id": i} for i in range(250)]},
    )
    dst = FakeConn(tables={"shop.orders": [{"COLUMN_NAME": "id"}]})
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select("SELECT * FROM shop.orders o")
    results = m.migrate(cfg_b, cfg_a, plan, batch_size=100)

    assert results[0].replaced == 250
    assert [len(b) for b in dst.batches] == [100, 100, 50]

def test_migrate_error_names_origin_and_destination(fake_connections):
    """Cuando no hay columnas en común, el error tiene que dizer cuál es cuál.

    Antes decía "Ninguna columna de yappy.orders existe en yappy.orders": el
    mismo nombre dos veces, sin distinguir origen de destino, y se leía como un
    bug del migrador en vez de como "estas dos tablas no se parecen en nada".
    """
    src = FakeConn(
        tables={"yappy.orders": [{"COLUMN_NAME": "order_id"}, {"COLUMN_NAME": "channel"}]},
        rows={"SELECT DISTINCT `yappy`.`orders`.*": [{"order_id": 1, "channel": "web"}]},
    )
    dst = FakeConn(tables={"yappy.orders": [{"COLUMN_NAME": "id"}]})
    cfg_b, cfg_a = fake_connections(src, dst)

    plan = parse_select("SELECT * FROM yappy.orders")
    results = m.migrate(cfg_b, cfg_a, plan)

    assert results[0].ok is False
    err = results[0].error
    assert "origen: src" in err
    assert "destino: dst" in err
    # La tabla se nombra dos veces, pero cada vez con su rol explícito.
    assert err.count("yappy.orders") == 2
    assert "no se copió ninguna fila" in err
    # Las columnas del origen que no existen en el destino se reportan aparte.
    assert sorted(results[0].skipped_columns) == ["channel", "order_id"]
    # No se intentó escribir nada.
    assert dst.batches == []
