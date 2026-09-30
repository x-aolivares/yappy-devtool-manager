"""Tests del guard de solo-lectura.

Cada caso existe porque hay una forma concreta de evadir un check ingenuo.
"""
import pytest

from web.api.domain.exceptions import UnsafeQueryError
from web.api.domain.sql_guard import assert_read_only, strip_comments


# --- must pass ------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from orders",
        "  SELECT a, b FROM t JOIN u ON t.id = u.id  ",
        "SHOW DATABASES",
        "SHOW CREATE TABLE orders",
        "DESCRIBE orders",
        "desc orders",
        "EXPLAIN SELECT 1",
        "WITH recent AS (SELECT * FROM o) SELECT * FROM recent",
        "SELECT 1;",
        "SELECT 1;   \n  ",
        "/* leading comment */ SELECT 1",
        "-- leading comment\nSELECT 1",
        "# leading comment\nSELECT 1",
        "SELECT\n  a\nFROM t",
    ],
)
def test_allows_read_only(sql):
    assert_read_only(sql)


# --- must be rejected -----------------------------------------------------


@pytest.mark.parametrize(
    "sql,fragment",
    [
        ("DROP TABLE orders", "not allowed"),
        ("DELETE FROM orders", "not allowed"),
        ("UPDATE orders SET x = 1", "not allowed"),
        ("INSERT INTO orders VALUES (1)", "not allowed"),
        ("TRUNCATE orders", "not allowed"),
        ("ALTER TABLE orders ADD COLUMN x INT", "not allowed"),
        ("CREATE TABLE t (id INT)", "not allowed"),
        ("GRANT ALL ON *.* TO 'x'@'%'", "not allowed"),
        ("CALL some_procedure()", "not allowed"),
        ("SET GLOBAL x = 1", "not allowed"),
        ("LOAD DATA INFILE 'x' INTO TABLE t", "not allowed"),
    ],
)
def test_rejects_writes(sql, fragment):
    with pytest.raises(UnsafeQueryError) as info:
        assert_read_only(sql)
    assert fragment in str(info.value)


# --- the actual bypasses --------------------------------------------------


def test_rejects_leading_comment_hiding_a_write():
    """`sql.upper().startswith('SELECT')` lets this one through only if the
    comment is stripped first — it must be rejected."""
    with pytest.raises(UnsafeQueryError) as info:
        assert_read_only("-- SELECT * FROM t\nDROP TABLE orders")
    assert "not allowed" in str(info.value)


def test_rejects_block_comment_hiding_a_write():
    with pytest.raises(UnsafeQueryError):
        assert_read_only("/* SELECT */ DELETE FROM orders")


def test_rejects_stacked_statement():
    with pytest.raises(UnsafeQueryError) as info:
        assert_read_only("SELECT 1; DROP TABLE orders")
    assert "Multiple statements" in str(info.value)


def test_rejects_stacked_statement_with_comment_separator():
    with pytest.raises(UnsafeQueryError):
        assert_read_only("SELECT 1; /* nothing */ DROP TABLE orders")


def test_semicolon_inside_a_string_literal_is_not_a_separator():
    """A naive `sql.split(';')` would reject this perfectly valid query."""
    assert_read_only("SELECT 'DROP TABLE orders' AS harmless")
    assert_read_only("SELECT 1 WHERE note = 'a;b;c'")


def test_semicolon_inside_a_literal_cannot_hide_a_second_statement():
    with pytest.raises(UnsafeQueryError):
        assert_read_only("SELECT 'a;b'; DROP TABLE orders")


def test_backtick_identifier_with_semicolon_is_not_a_separator():
    assert_read_only("SELECT `we;ird` FROM t")


def test_doubled_quote_escape_inside_literal():
    # 'it''s; fine' -> the ';' belongs to the literal
    assert_read_only("SELECT 'it''s; fine' AS s")


def test_backslash_escape_inside_literal():
    assert_read_only(r"SELECT 'a\'; b' AS s")


def test_empty_and_comment_only_are_rejected():
    for sql in ("", "   ", "-- just a comment", "/* nothing */"):
        with pytest.raises(UnsafeQueryError):
            assert_read_only(sql)


def test_oversized_query_is_rejected():
    with pytest.raises(UnsafeQueryError) as info:
        assert_read_only("SELECT " + "a," * 60_000 + "a")
    assert "too long" in str(info.value)


def test_parenthesised_select_is_allowed_and_does_not_crash():
    """MySQL has no `(DELETE ...)`, so a leading paren can't smuggle a write —
    and `(SELECT..) UNION (SELECT..)` is legitimately read-only."""
    assert_read_only("(SELECT 1) UNION (SELECT 2)")


# --- comment stripping -------------------------------------------------


def test_strip_comments_preserves_string_content():
    sql = "SELECT '-- not a comment', '/* nor this */' FROM t"
    assert strip_comments(sql) == sql


def test_strip_comments_handles_unterminated_block_comment():
    assert "DROP" not in strip_comments("/* never closed\nDROP TABLE t")
