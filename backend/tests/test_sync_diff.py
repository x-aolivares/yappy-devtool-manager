import pytest

from yappy_library.application.database.sync import db_objects as obj
from yappy_library.application.database.sync import ddl
from yappy_library.application.database.sync import diff


def test_normalize_ddl_strips_definer_and_whitespace():
    a = "CREATE DEFINER=`u`@`h` PROCEDURE p()\n  BEGIN\n    SELECT 1;\n  END"
    b = "  create \n  procedure p() begin select 1; end   "
    assert obj.normalize_ddl(a) == obj.normalize_ddl(b)


def test_normalize_ddl_strips_comments_and_case():
    a = "CREATE TABLE t (/* pk */ id INT PRIMARY KEY)"
    b = "create table t ( id int primary key)"
    assert obj.normalize_ddl(a) == obj.normalize_ddl(b)


def test_normalize_ddl_strips_current_user_definer():
    a = "CREATE DEFINER=CURRENT_USER PROCEDURE p() BEGIN END"
    b = "CREATE PROCEDURE p() BEGIN END"
    assert obj.normalize_ddl(a) == obj.normalize_ddl(b)


@pytest.fixture
def col():
    def _col(
        name,
        col_type="int",
        nullable="NO",
        default=None,
        extra="",
        charset=None,
        collation=None,
        comment="",
    ):
        return {
            "COLUMN_NAME": name,
            "COLUMN_TYPE": col_type,
            "IS_NULLABLE": nullable,
            "COLUMN_DEFAULT": default,
            "EXTRA": extra,
            "CHARACTER_SET_NAME": charset,
            "COLLATION_NAME": collation,
            "COLUMN_COMMENT": comment,
        }

    return _col


def test_diff_tables_added_removed_modified(col):
    a = [col("id"), col("name", "varchar(50)"), col("old")]
    b = [col("id"), col("name", "varchar(100)"), col("new")]

    col_ops, index_ops = diff.diff_tables(a, b, [], [])

    by_name = {op.op: op for op in col_ops}
    assert by_name["added"].name == "new"
    assert by_name["removed"].name == "old"
    assert by_name["modified"].name == "name"
    assert not index_ops


def test_diff_tables_equal(col):
    a = [col("id"), col("name", "varchar(50)")]
    b = [col("id"), col("name", "varchar(50)")]

    col_ops, index_ops = diff.diff_tables(a, b, [], [])

    assert col_ops == []
    assert index_ops == []


def test_diff_tables_index_added_removed():
    def idx(name, non_unique, cols):
        return [
            {
                "INDEX_NAME": name,
                "NON_UNIQUE": non_unique,
                "SEQ_IN_INDEX": i + 1,
                "COLUMN_NAME": c,
                "SUB_PART": None,
            }
            for i, c in enumerate(cols)
        ]

    a = idx("idx_a", 1, ["a"])
    b = idx("idx_a", 1, ["a"]) + idx("idx_b", 0, ["b", "c"])

    col_ops, index_ops = diff.diff_tables([], [], a, b)

    assert ("added", "idx_b", (0, ((1, "b", None), (2, "c", None)))) == index_ops[0]
    # only idx_b added; idx_a identical so no changes for it
    assert len(index_ops) == 1
    assert col_ops == []


def test_alter_table_script_generates_clauses(col):
    col_ops = [
        diff.ColumnOp("added", "new", col("new")),
        diff.ColumnOp("removed", "old", col("old")),
        diff.ColumnOp("modified", "name", col("name", "varchar(100)")),
    ]

    sql = ddl.alter_table_script("myschema", "t", col_ops, [])

    assert "ALTER TABLE `myschema`.`t`" in sql
    assert "ADD COLUMN `new` int NOT NULL" in sql
    assert "DROP COLUMN `old`" in sql
    assert "MODIFY COLUMN `name` varchar(100) NOT NULL" in sql


def test_alter_table_script_defaults(col):
    col_ops = [
        diff.ColumnOp("added", "rate", col("rate", "decimal(5,2)", default="0.00")),
        diff.ColumnOp("added", "name", col("name", "varchar(50)", nullable="YES", default="Guest")),
        diff.ColumnOp(
            "added",
            "ts",
            col("ts", "timestamp", nullable="YES", default="CURRENT_TIMESTAMP", extra="DEFAULT_GENERATED"),
        ),
    ]

    sql = ddl.alter_table_script("s", "t", col_ops, [])

    assert "DEFAULT 0.00" in sql
    assert "DEFAULT 'Guest'" in sql
    assert "DEFAULT CURRENT_TIMESTAMP" in sql


def test_alter_table_script_indexes():
    index_ops = [
        ("added", "idx_name", (0, ((1, "name", None),))),
        ("removed", "idx_old", (1, ((1, "old", None),))),
        ("modified", "PRIMARY", (0, ((1, "id", None),))),
    ]

    sql = ddl.alter_table_script("s", "t", [], index_ops)

    assert "ADD UNIQUE INDEX `idx_name` (`name`)" in sql
    assert "DROP INDEX `idx_old`" in sql
    assert "DROP PRIMARY KEY" in sql
    assert "ADD PRIMARY KEY (`id`)" in sql


def test_create_table_script_passthrough():
    create = "CREATE TABLE `x` (`id` int NOT NULL) ENGINE=InnoDB\n"
    assert ddl.create_table_script(create).endswith("ENGINE=InnoDB")
    assert not ddl.create_table_script(create).endswith(";")


def test_replace_procedure_script_drops_then_creates():
    """MySQL has no CREATE OR REPLACE PROCEDURE, so replacing is DROP + CREATE.

    Regression: this used to emit ``CREATE OR REPLACE PROCEDURE``, which the
    server rejects with a 1064 -- compiling a procedure never worked. The old
    test asserted ``startswith("CREATE OR REPLACE PROCEDURE")`` and therefore
    pinned the defect as intended behavior.
    """
    create = (
        "CREATE DEFINER=`u`@`h` PROCEDURE `calc`(x int)\n"
        "BEGIN\nSET @a = x;\nEND"
    )
    script = ddl.replace_procedure_script(create, "yappy", "calc")
    assert script.startswith("DROP PROCEDURE IF EXISTS `yappy`.`calc`;")
    assert "CREATE OR REPLACE" not in script
    assert "DEFINER" not in script
    assert script.splitlines()[1].startswith("CREATE PROCEDURE `calc`(x int)")


def test_replace_procedure_script_keeps_line_structure():
    """Stripping DEFINER must not flatten the body onto a single line.

    Regression: the old ``re.sub(r"\\s+", " ", text)`` collapse produced one
    unreadable line, which is useless in a textarea the user has to edit.
    """
    create = (
        "CREATE DEFINER=`root`@`%` PROCEDURE `calc`(\n"
        "  IN  x INT,\n"
        "  OUT y DECIMAL(12,2)\n"
        ")\n"
        "BEGIN\n"
        "  DECLARE v DECIMAL(12,2) DEFAULT 0.00;\n"
        "\n"
        "  SELECT COALESCE(SUM(x), 0)\n"
        "    INTO v;\n"
        "\n"
        "  SET y = v;\n"
        "END"
    )
    script = ddl.replace_procedure_script(create, "yappy", "calc")

    assert script.startswith("DROP PROCEDURE IF EXISTS `yappy`.`calc`;\n")
    assert "DEFINER" not in script
    # The signature stays spread over its own lines.
    assert "\n  IN  x INT,\n" in script
    assert "\n  OUT y DECIMAL(12,2)\n" in script
    # The body keeps its newlines and the blank line that separates the DECLARE.
    assert "\n  DECLARE v DECIMAL(12,2) DEFAULT 0.00;\n\n" in script
    assert "\n    INTO v;\n" in script
    assert script.endswith("END")
    assert script.count("\n") >= 10


def test_create_procedure_script_keeps_create():
    create = "CREATE DEFINER=`u`@`h` PROCEDURE `calc`(x int) BEGIN END"
    script = ddl.create_procedure_script(create)
    assert script.startswith("CREATE PROCEDURE `calc`(x int)")
    assert "DEFINER" not in script


class _SchemaCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None
        self.params = None

    def execute(self, sql, params=None):
        self.executed = sql
        self.params = params

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SchemaConn:
    def __init__(self, rows):
        self._cursor = _SchemaCursor(rows)

    def cursor(self, *args, **kwargs):
        return self._cursor


def test_list_schemas_returns_schemas_in_order():
    conn = _SchemaConn([("alpha",), ("beta",)])
    assert obj.list_schemas(conn) == ["alpha", "beta"]


def test_list_schemas_excludes_system_schemas():
    conn = _SchemaConn([])
    obj.list_schemas(conn)
    sql = conn._cursor.executed
    assert "INFORMATION_SCHEMA.SCHEMATA" in sql
    for system in ("information_schema", "mysql", "performance_schema", "sys"):
        assert f"'{system}'" in sql


def test_list_tables_returns_base_tables_of_the_schema():
    conn = _SchemaConn([("orders",), ("users",)])
    assert obj.list_tables(conn, "yappy") == ["orders", "users"]
    sql, params = conn._cursor.executed, conn._cursor.params
    assert "INFORMATION_SCHEMA.TABLES" in sql
    assert "TABLE_TYPE = 'BASE TABLE'" in sql
    assert "ORDER BY TABLE_NAME" in sql
    assert params == ("yappy",)
    for system in ("information_schema", "mysql", "performance_schema", "sys"):
        assert f"'{system}'" in sql


def test_list_procedures_returns_procedures_of_the_schema():
    conn = _SchemaConn([("sp_calc",)])
    assert obj.list_procedures(conn, "yappy") == ["sp_calc"]
    sql, params = conn._cursor.executed, conn._cursor.params
    assert "INFORMATION_SCHEMA.ROUTINES" in sql
    assert "ROUTINE_TYPE = 'PROCEDURE'" in sql
    assert "ORDER BY ROUTINE_NAME" in sql
    assert params == ("yappy",)
    for system in ("information_schema", "mysql", "performance_schema", "sys"):
        assert f"'{system}'" in sql
