import contextlib

import pytest

from yappy_library.application.database.sync import exec as dbexec
from yappy_library.application.database.sync.exec import split_statements
from yappy_library.adapters.database.connection import SyncError


# --- split_statements -----------------------------------------------------


def test_split_single_statement():
    assert split_statements("ALTER TABLE `yappy`.`t` ADD COLUMN `x` INT NOT NULL;") == [
        "ALTER TABLE `yappy`.`t` ADD COLUMN `x` INT NOT NULL"
    ]


def test_split_multiple_statements():
    assert split_statements("CREATE TABLE a (id INT);\nALTER TABLE b ADD COLUMN c INT;") == [
        "CREATE TABLE a (id INT)",
        "ALTER TABLE b ADD COLUMN c INT",
    ]


def test_procedure_keeps_internal_semicolons():
    code = "CREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END"
    stmts = split_statements(code)
    assert len(stmts) == 1
    assert stmts[0].startswith("CREATE PROCEDURE p()")
    assert "SELECT 1; SELECT 2;" in stmts[0]


def test_procedure_with_end_if_does_not_close_depth():
    code = (
        "CREATE PROCEDURE p() BEGIN "
        "IF a THEN SET x = 1; "
        "ELSE SET x = 2; "
        "END IF; "
        "SELECT x; "
        "END"
    )
    stmts = split_statements(code)
    assert len(stmts) == 1
    assert "END IF; SELECT x;" in stmts[0]


def test_procedure_with_case_expression_stays_one_statement():
    """The `END` of a CASE expression closes nothing.

    Regression: every `(CASE ... END)` used to lower the BEGIN counter, and a
    procedure with a handful of them fell back to depth 0 halfway through its
    body — so the `;` inside the routine started splitting it and MySQL got a
    `CREATE PROCEDURE` with the body cut in half. See the ledger procedure that
    has one per SELECT.
    """
    code = (
        "CREATE PROCEDURE p() BEGIN "
        "DECLARE alias VARCHAR(64); "
        "SELECT (CASE WHEN a = 1 THEN 'x' ELSE 'y' END) AS alias, "
        "       (CASE WHEN b = 2 THEN 'z' ELSE 'w' END) AS other "
        "FROM t; "
        "SET alias = 'fin'; "
        "END"
    )
    stmts = split_statements(code)
    assert len(stmts) == 1
    assert stmts[0].count(";") == 3


def test_compound_case_statement_keeps_depth_balanced():
    """`CASE ... END CASE` is a statement, not an expression: it is counted once
    and closed by its own `END CASE`, leaving the BEGIN counter untouched."""
    code = (
        "CREATE PROCEDURE p() BEGIN "
        "CASE WHEN a THEN SET x = 1; ELSE SET x = 2; END CASE; "
        "SELECT CASE WHEN b THEN 1 END; "
        "END"
    )
    stmts = split_statements(code)
    assert len(stmts) == 1
    assert "END CASE;" in stmts[0]


def test_case_expression_outside_any_block():
    code = "SELECT CASE WHEN a THEN 'x' ELSE 'y' END; SELECT 2;"
    assert split_statements(code) == ["SELECT CASE WHEN a THEN 'x' ELSE 'y' END", "SELECT 2"]


def test_bare_begin_is_a_transaction_and_does_not_swallow_the_script():
    """`BEGIN;` never closes. Counting it would glue everything after it into
    the first statement, which the server rejects."""
    code = "BEGIN; INSERT INTO t VALUES (1); COMMIT; SELECT 2;"
    assert split_statements(code) == [
        "BEGIN",
        "INSERT INTO t VALUES (1)",
        "COMMIT",
        "SELECT 2",
    ]


def test_procedure_body_with_handler_and_case_is_not_split():
    """The shape that broke in production: a SQLEXCEPTION handler (a nested
    BEGIN...END) plus CASE expressions in the same body."""
    code = (
        "CREATE PROCEDURE `yappy_payment`.`p`(IN i_date DATETIME)\n"
        "BEGIN\n"
        "    DECLARE w_code VARCHAR(10) DEFAULT 'TXN-000';\n"
        "    DECLARE EXIT handler FOR SQLEXCEPTION\n"
        "    BEGIN\n"
        "        SET w_code = 'ERR';\n"
        "    END;\n"
        "    SELECT (CASE WHEN py_status = 'COMPLETED' THEN 1 ELSE 0 END) AS ok\n"
        "    FROM yappy_payment.ledger\n"
        "    WHERE l_cutoff_date = DATE(i_date);\n"
        "    SET w_code = 'fin';\n"
        "END"
    )
    stmts = split_statements(code)
    assert len(stmts) == 1
    assert stmts[0].startswith("CREATE PROCEDURE `yappy_payment`.`p`")
    assert stmts[0].endswith("END")


def test_delimiter_directive_custom_and_reset():
    code = (
        "DELIMITER $$\n"
        "CREATE PROCEDURE p()\n"
        "BEGIN\n"
        "  SELECT 1;\n"
        "  SELECT 2;\n"
        "END$$\n"
        "DELIMITER ;\n"
        "SELECT 3;"
    )
    stmts = split_statements(code)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE PROCEDURE p()")
    assert stmts[0].endswith("END")
    assert stmts[1] == "SELECT 3"


def test_quoted_semicolons_and_backticks_not_split():
    code = "INSERT INTO t VALUES ('a;b', `c;d`); CREATE TABLE x (id INT);"
    stmts = split_statements(code)
    assert stmts[0] == "INSERT INTO t VALUES ('a;b', `c;d`)"
    assert stmts[1] == "CREATE TABLE x (id INT)"


def test_comments_removed():
    code = (
        "-- line comment\n"
        "ALTER TABLE t ADD COLUMN a INT; # trailing\n"
        "/* block ; with semicolon */\n"
        "CREATE TABLE x (id INT);"
    )
    stmts = split_statements(code)
    assert stmts == ["ALTER TABLE t ADD COLUMN a INT", "CREATE TABLE x (id INT)"]


def test_empty_or_only_comments():
    assert split_statements("") == []
    assert split_statements("   \n  \t  ") == []
    assert split_statements("-- nada\n/* tampoco */\n") == []


# --- execute_sql -----------------------------------------------------------


class FakeCursor:
    def __init__(self, fail_on=None):
        self.executed = []
        self.fail_on = fail_on
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom: " + sql)
        self.executed.append(sql)
        if sql.startswith("INSERT"):
            self.rowcount = 5
        else:
            self.rowcount = -1


class FakeConn:
    def __init__(self, fail_on=None):
        self.cur = FakeCursor(fail_on)

    def cursor(self):
        return self.cur


@pytest.fixture
def fake_connect(monkeypatch):
    def make(fail_on=None):
        conn = FakeConn(fail_on)
        store = {}

        @contextlib.contextmanager
        def _connect(_cfg):
            store["conn"] = conn
            yield conn

        monkeypatch.setattr(dbexec, "connect", _connect)
        return conn, store

    return make


def test_execute_sql_uses_schema_and_runs_statements(fake_connect):
    conn, _ = fake_connect()
    results = dbexec.execute_sql(
        object(),
        schema="myschema",
        code="CREATE TABLE t (id INT); INSERT INTO t VALUES (1);",
    )

    assert conn.cur.executed[0] == "USE `myschema`"
    assert conn.cur.executed[1] == "CREATE TABLE t (id INT)"
    assert conn.cur.executed[2] == "INSERT INTO t VALUES (1)"
    assert all(r.ok for r in results)
    assert results[0].affected == -1
    assert results[1].affected == 5


def test_execute_sql_without_schema_no_use(fake_connect):
    conn, _ = fake_connect()
    dbexec.execute_sql(object(), schema="", code="SELECT 1;")
    assert conn.cur.executed == ["SELECT 1"]


def test_execute_sql_captures_error_and_continues(fake_connect):
    conn, _ = fake_connect(fail_on="DROP")
    results = dbexec.execute_sql(
        object(), schema="", code="INSERT INTO t VALUES (1); DROP TABLE t; INSERT INTO t VALUES (2);"
    )

    assert len(results) == 3
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error == "boom: DROP TABLE t"
    assert conn.cur.executed == ["INSERT INTO t VALUES (1)", "INSERT INTO t VALUES (2)"]


def test_execute_sql_empty_code_raises(fake_connect):
    fake_connect()
    with pytest.raises(SyncError):
        dbexec.execute_sql(object(), schema="", code="  -- solo comentario\n  ")
