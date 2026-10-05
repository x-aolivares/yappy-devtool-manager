"""Read-only query execution for the "Ejecutar SQL" page (consultar)."""

import contextlib

import pytest

from yappy_library.application.database.sync import query as q
from yappy_library.adapters.database.connection import SyncError


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.rowcount = owner.rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self.owner.executed.append(sql)
        if self.owner.raise_on and self.owner.raise_on in sql:
            raise RuntimeError("Unknown column 'x'")
        self._rows = self.owner.rows
        self.rowcount = self.owner.rowcount

    def fetchmany(self, size):
        return list(self.owner.rows[:size])

    def nextset(self):
        # `CALL` puede devolver varios result sets seguidos y el cursor se avanza
        # al siguiente. Sin `result_sets` hay uno solo, así que esto es el fin.
        self.owner.sets_read += 1
        if self.owner.sets_read >= len(self.owner.result_sets or [1]):
            return False
        self.owner._use_set(self.owner.sets_read)
        return True

    @property
    def description(self):
        return self.owner.description


class FakeConn:
    def __init__(self, rows=None, columns=("id", "name"), raise_on=None, result_sets=None):
        self.rows = rows if rows is not None else []
        self.description = [(c, None, None, None, None, None, None) for c in columns]
        self.raise_on = raise_on
        self.executed = []
        #: `rowcount` de MySQL: las filas que la última sentencia tocó. Sólo
        #: importa para un `CALL` que no devuelve filas.
        self.rowcount = 0
        #: Los result sets que un `CALL` puede devolver seguidos, cada uno con sus
        #: filas y sus columnas. `None` = un solo set, el caso normal de un SELECT.
        self.result_sets = result_sets
        self.sets_read = 0
        if result_sets:
            self._use_set(0)

    def _use_set(self, index: int) -> None:
        rs = self.result_sets[index]
        self.rows = rs.get("rows", [])
        self.description = [
            (c, None, None, None, None, None, None) for c in rs.get("columns", ())
        ]

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


def _one_set(columns, rows):
    """A connection that answers with exactly one result set."""
    return FakeConn(rows=rows, columns=columns)


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
    # `CALL` sí entra (abajo hay tests de eso) porque es la vía para correr un
    # stored procedure. Estas son las escrituras que siguen sin entrar.
    for sql in ("DELETE FROM t", "UPDATE t SET a = 1", "DROP TABLE t", "INSERT INTO t VALUES (1)"):
        with pytest.raises(q.QueryError) as exc:
            q.ensure_single_read_statement(sql)
        assert "consultas de lectura" in str(exc.value)


def test_a_stored_procedure_call_is_accepted():
    for sql in (
        "CALL sp_pagos('2026-03-01')",
        "call sp_pagos()",
        "EXEC sp_pagos",
        "EXECUTE sp_pagos @fecha = '2026-03-01'",
    ):
        assert q.ensure_single_read_statement(sql) == sql


def test_the_rejection_names_both_ways_in():
    """El error tiene que decir qué se admite, no sólo qué no: si alguien pega
    un `TRUNCATE` y ve "no se admiten consultas de lectura" no sabe que existe
    una salida para lo que sí quiere hacer."""
    with pytest.raises(q.QueryError) as exc:
        q.ensure_single_read_statement("TRUNCATE TABLE t")
    message = str(exc.value)
    assert "CALL" in message
    assert "SELECT" in message


def test_a_call_with_several_statements_is_still_rejected():
    with pytest.raises(q.QueryError) as exc:
        q.ensure_single_read_statement("CALL sp_a(); CALL sp_b();")
    assert "2 sentencias" in str(exc.value)


# --- stored procedures -------------------------------------------------------


def test_a_call_shows_the_rows_of_its_result_set(use_conn):
    conn = use_conn(rows=[{"pedido_id": 7, "estado": "OK"}])
    conn.description = [("pedido_id", None, None, None, None, None, None), ("estado", None, None, None, None, None, None)]

    result = q.run_select(object(), "CALL sp_reporte()")

    assert result.columns == ["pedido_id", "estado"]
    assert result.rows == [{"pedido_id": 7, "estado": "OK"}]


def test_a_call_skips_the_result_sets_that_have_no_columns(use_conn):
    """Un procedure que hace su trabajo y recién después devuelve la tabla: leer
    sólo el primer result set mostraría la fila de estado, que no dice nada."""
    use_conn(
        result_sets=[
            {"columns": (), "rows": []},  # el OUT param / la fila de estado
            {"columns": ("id",), "rows": [{"id": 1}, {"id": 2}]},
        ]
    )

    result = q.run_select(object(), "CALL sp_reporte()")

    assert result.columns == ["id"]
    assert result.rows == [{"id": 1}, {"id": 2}]


def test_reading_a_call_stops_at_the_first_set_with_columns(use_conn):
    """No drena los sets que vienen después, y no debería: la conexión es de esta
    request y se cierra al terminar, así que un set sin leer no llega a otro
    consulta. Lo que importa es que no se avanza de más."""
    conn = use_conn(
        result_sets=[
            {"columns": (), "rows": []},
            {"columns": ("id",), "rows": [{"id": 1}]},
            {"columns": ("otro",), "rows": [{"otro": 2}]},
        ]
    )

    result = q.run_select(object(), "CALL sp_reporte()")

    assert result.columns == ["id"]
    # Un `nextset` de más: se llega al que tiene columnas y se corta.
    assert conn.sets_read == 1


def test_a_call_that_returns_nothing_says_it_ran(use_conn):
    """Un procedure que escribe y no devuelve nada es un resultado legítimo.
    Reportarlo como "0 filas" se lee como si la consulta no hubiera encontrado
    datos, que es otra cosa."""
    conn = use_conn(rows=[])
    conn.description = []
    conn.rowcount = 12

    result = q.run_select(object(), "CALL sp_migrar()")

    assert len(result.rows) == 1
    assert "se ejecutó" in result.rows[0]["mensaje"]
    assert "12" in result.rows[0]["mensaje"]


def test_a_select_with_no_rows_is_still_reported_as_zero(use_conn):
    """La distinguishes con un SELECT vacío: 0 filas de datos es un dato."""
    use_conn(rows=[])

    result = q.run_select(object(), "SELECT id FROM t WHERE 0")

    assert result.rows == []
    assert result.total == 0


def test_a_failing_call_is_a_sync_error(use_conn):
    conn = use_conn(rows=[])
    conn.raise_on = "CALL"

    with pytest.raises(SyncError) as exc:
        q.run_select(object(), "CALL sp_que_no_existe()")

    assert "Unknown column" in str(exc.value) or "sp_que_no_existe" in str(exc.value)


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


# --- statement timeout ------------------------------------------------------


class _Cfg:
    """A config whose only relevant key is ``DB_QUERY_TIMEOUT_MS``."""

    def __init__(self, value=None):
        self._value = value

    def get(self, key, default=None):
        return self._value if key == "DB_QUERY_TIMEOUT_MS" and self._value is not None else default


def test_the_session_is_capped_before_the_statement_runs(use_conn):
    """MySQL corta la sentencia y el error lo dice; un timeout del cliente sólo
    abandona y deja la consulta corriendo en el servidor."""
    conn = use_conn(rows=[])

    q.run_select(object(), "SELECT 1")

    assert any("max_execution_time" in stmt for stmt in conn.executed)
    assert conn.executed.index("SELECT 1") > 0, "el tope va antes de la consulta"


def test_the_default_cap_is_the_module_default():
    assert q._statement_timeout_ms(object()) == q.DEFAULT_STATEMENT_TIMEOUT_MS
    assert q._statement_timeout_ms(_Cfg()) == q.DEFAULT_STATEMENT_TIMEOUT_MS


def test_the_cap_can_be_overridden_per_environment():
    assert q._statement_timeout_ms(_Cfg("5000")) == 5000
    assert q._statement_timeout_ms(_Cfg(" 5000 ")) == 5000


def test_an_unparseable_cap_degrades_to_the_default_instead_of_raising():
    assert q._statement_timeout_ms(_Cfg("pronto")) == q.DEFAULT_STATEMENT_TIMEOUT_MS


def test_zero_disables_the_cap(use_conn):
    """Consultas que legítimamente tardan: es una decisión del ambiente, no un error."""
    conn = use_conn(rows=[])

    q.run_select(_Cfg("0"), "SELECT 1")

    assert not any("max_execution_time" in stmt for stmt in conn.executed)


def test_a_server_without_the_variable_still_runs_the_query(use_conn):
    """Aurora MySQL 5.7 no conoce `max_execution_time`. Perder el tope es
    aceptable; perder la consulta no — el read timeout sigue como red de
    seguridad."""
    conn = use_conn(rows=[])
    original = conn.cursor

    def _cursor(dict_mode=False):
        cur = original(dict_mode=dict_mode)
        real_execute = cur.execute

        def _execute(sql, args=None):
            if "max_execution_time" in sql:
                raise RuntimeError("Unknown system variable 'max_execution_time'")
            return real_execute(sql)

        cur.execute = _execute
        return cur

    conn.cursor = _cursor

    result = q.run_select(object(), "SELECT id FROM t")

    assert result.columns == ["id", "name"]
    assert conn.executed == ["SELECT id FROM t"]