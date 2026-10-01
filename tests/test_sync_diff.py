import pytest

from yappy_cli.sync import db_objects as obj
from yappy_cli.sync import ddl
from yappy_cli.sync import diff


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


def test_replace_procedure_script_strips_definer():
    create = (
        "CREATE DEFINER=`u`@`h` PROCEDURE `calc`(x int)\n"
        "BEGIN\nSET @a = x;\nEND"
    )
    script = ddl.replace_procedure_script(create)
    assert script.startswith("CREATE OR REPLACE PROCEDURE `calc`(x int)")
    assert "DEFINER" not in script


def test_create_procedure_script_keeps_create():
    create = "CREATE DEFINER=`u`@`h` PROCEDURE `calc`(x int) BEGIN END"
    script = ddl.create_procedure_script(create)
    assert script.startswith("CREATE PROCEDURE `calc`(x int)")
    assert "DEFINER" not in script