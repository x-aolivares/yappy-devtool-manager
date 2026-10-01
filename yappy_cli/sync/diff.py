"""Comparison logic between two regions. Direction is always B -> A (B is the
source of truth; A is the region to update)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ColumnOp:
    op: str  # "added" | "removed" | "modified"
    name: str
    definition: dict | None = None
    note: str | None = None


def _column_signature(row: dict) -> tuple:
    return (
        row["COLUMN_TYPE"],
        row["IS_NULLABLE"],
        row["COLUMN_DEFAULT"],
        row["EXTRA"],
        row["CHARACTER_SET_NAME"],
        row["COLLATION_NAME"],
        row["COLUMN_COMMENT"],
    )


def group_indexes(rows: list[dict]) -> dict[str, tuple]:
    """Index name -> (non_unique, ((seq, column, sub_part), ...))."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["INDEX_NAME"], []).append(row)
    result: dict[str, tuple] = {}
    for name, rows_for_name in groups.items():
        rows_for_name.sort(key=lambda r: r["SEQ_IN_INDEX"])
        result[name] = (
            int(rows_for_name[0]["NON_UNIQUE"]),
            tuple(
                (r["SEQ_IN_INDEX"], r["COLUMN_NAME"], r["SUB_PART"])
                for r in rows_for_name
            ),
        )
    return result


def diff_tables(
    cols_a: list[dict],
    cols_b: list[dict],
    idx_a: list[dict],
    idx_b: list[dict],
) -> tuple[list[ColumnOp], list[tuple]]:
    """Compare columns and indexes of a table in both regions.

    Returns ``(column_ops, index_ops)`` where index_ops are
    ``("added"|"removed"|"modified", index_name, signature)``.
    """
    a = {r["COLUMN_NAME"]: r for r in cols_a}
    b = {r["COLUMN_NAME"]: r for r in cols_b}

    column_ops: list[ColumnOp] = []
    for name in b:
        if name not in a:
            column_ops.append(ColumnOp("added", name, b[name]))
    for name in a:
        if name not in b:
            column_ops.append(ColumnOp("removed", name, a[name]))
    for name in b:
        if name in a and _column_signature(a[name]) != _column_signature(b[name]):
            column_ops.append(ColumnOp("modified", name, b[name], "definition differs"))

    sig_a = group_indexes(idx_a)
    sig_b = group_indexes(idx_b)

    index_ops: list[tuple] = []
    for name in sig_b:
        if name not in sig_a:
            index_ops.append(("added", name, sig_b[name]))
    for name in sig_a:
        if name not in sig_b:
            index_ops.append(("removed", name, sig_a[name]))
    for name in sig_b:
        if name in sig_a and sig_a[name] != sig_b[name]:
            index_ops.append(("modified", name, sig_b[name]))

    return column_ops, index_ops