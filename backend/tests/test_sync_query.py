"""Read-only query execution for the "Ejecutar SQL" page (consultar)."""

import contextlib

import pytest

from yappy_library.application.database.sync import query as q
from yappy_library.adapters.database.connection import SyncError


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.owner.executed.append(sql)
        if self.owner.raise_on and self.owner.raise_on in sql:
            raise RuntimeError("Unknown column 'x'")
        self._rows = self.owner.rows

    def fetchmany(self, size):
        return list(self.owner.rows[:size])

    @property
    def description(self):
        return self.owner.description


class FakeConn:
    def __init__(self, rows=None, columns=("id", "name"), raise_on=None):
        self.rows = rows if rows is not None else []
        self.description = [(c, None, None, None, None, None, None) for c in columns]
        self.raise_on = raise_on
        self.executed = []

    def cursor(self, dict_mode=False):
        return FakeCursor(self)

    def close(self):
        pass


@pytest.fixture
def use_conn(monkeypatch):
    """Point ``query.connect`` at a fake connection for the duration of a test."""

    def _install(**kwargs) -> FakeConn:
        conn = FakeConn(**kwargs)

        @contextlib.contextmanager
        def _connect(_cfg):
            yield conn

        monkeypatch.setattr(q, "connect", lambda cfg: _connect(cfg))
        return conn

    return _install


# --- validation -------------------------------------------------------------


def test_empty_input_is_rejected():
    with pytest.raises(q.QueryError) as exc:
        q.ensure_single_read_statement("   ")
    assert "Pegá la consulta" in str(exc.value)


def test_multiple_statements_are_rejected():
    with pytest.raises(q.QueryError) as exc:
        q.ensure_single_read_statement("SELECT 1; SELECT 2;")
    assert "2 sentencias" in str(exc.value)


def test_write_statements_are_rejected():
    for sql in ("DELETE FROM t", "UPDATE t SET a = 1", "DROP TABLE t", "INSERT INTO t VALUES (1)"):
        with pytest.raises(q.QueryError) as exc:
            q.ensure_single_read_statement(sql)
        assert "consultas de lectura" in str(exc.value)


def test_read_statements_are_accepted():
    for sql in ("SELECT 1", "select * from t", "SHOW TABLES", "DESCRIBE t", "EXPLAIN SELECT 1"):
        assert q.ensure_single_read_statement(sql)


def test_comments_only_is_rejected():
    with pytest.raises(q.QueryError):
        q.ensure_single_read_statement("-- nada")


# --- run_select -------------------------------------------------------------


def test_returns_columns_and_rows(use_conn):
    use_conn(rows=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    result = q.run_select(object(), "SELECT id, name FROM t")

    assert result.columns == ["id", "name"]
    assert result.rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert result.total == 2
    assert result.truncated is False
    assert result.ms >= 0


def test_rows_are_capped_at_the_limit(use_conn):
    use_conn(rows=[{"id": i, "name": "x"} for i in range(10)])

    result = q.run_select(object(), "SELECT id, name FROM t", limit=4)

    assert len(result.rows) == 4
    assert result.truncated is True
    assert result.total is None


def test_empty_result_reports_zero(use_conn):
    use_conn(rows=[])

    result = q.run_select(object(), "SELECT 1 FROM t WHERE 0")

    assert result.rows == []
    assert result.total == 0
    assert result.truncated is False


def test_non_selectable_values_are_normalized(use_conn):
    import datetime
    import decimal

    use_conn(
        rows=[
            {
                "id": 1,
                "amount": decimal.Decimal("10.50"),
                "at": datetime.date(2026, 10, 1),
                "raw": b"\x00\xff",
            }
        ]
    )

    result = q.run_select(object(), "SELECT * FROM t")

    row = result.rows[0]
    assert row["amount"] == 10.5
    assert row["at"] == "2026-10-01"
    assert row["raw"] == "0x00ff"


def test_database_error_becomes_a_sync_error(use_conn):
    use_conn(raise_on="SELECT")

    with pytest.raises(SyncError) as exc:
        q.run_select(object(), "SELECT bad FROM t")

    assert "Unknown column" in str(exc.value)


def test_limit_is_clamped_to_the_hard_maximum(use_conn):
    use_conn(rows=[])
    assert q.MAX_ROWS == 500
    # An absurd limit must not be able to stream an unbounded result set.
    result = q.run_select(object(), "SELECT 1", limit=10_000)
    assert result.rows == []