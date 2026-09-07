"""Tests for fact_layer/table_structure.py (A6 — semantic table structure).

Synthetic Table fixtures — pure, deterministic transforms of an already-
built Table, no PDF I/O needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.parse import Table
from fact_layer.table_structure import (
    find_cells_matching_value,
    get_cell_context,
    structure_table,
)


def _revenue_table(unit="USD million"):
    return Table(
        page_no=1, bbox=(0, 0, 200, 100),
        rows=[["", "FY2024", "FY2025"], ["India", "500", "620"], ["US", "300", "350"]],
        cell_bboxes=[
            [(0, 0, 10, 10), (10, 0, 20, 10), (20, 0, 30, 10)],
            [(0, 10, 10, 20), (10, 10, 20, 20), (20, 10, 30, 20)],
            [(0, 20, 10, 30), (10, 20, 20, 30), (20, 20, 30, 30)],
        ],
        caption="Revenue", scale_context=unit,
    )


# --------------------------------------------------------------------------
# Simple table / headers / row labels / cell coordinates
# --------------------------------------------------------------------------

def test_simple_table_structure():
    st = structure_table(_revenue_table())
    assert st.header_row_count == 1
    assert st.column_headers == ["", "FY2024", "FY2025"]
    assert st.row_labels == ["India", "US"]


def test_column_header_reaches_the_cell():
    st = structure_table(_revenue_table())
    cell = get_cell_context(st, row=0, col=1)   # India, FY2024
    assert cell.text == "500"
    assert cell.column_header == "FY2024"
    assert cell.row_label == "India"


def test_row_label_reaches_every_cell_in_the_row():
    st = structure_table(_revenue_table())
    for col in range(3):
        assert get_cell_context(st, row=1, col=col).row_label == "US"


def test_cell_bbox_is_preserved():
    st = structure_table(_revenue_table())
    cell = get_cell_context(st, row=0, col=2)   # India, FY2025
    assert cell.bbox == (20, 10, 30, 20)


def test_out_of_range_cell_context_returns_none():
    st = structure_table(_revenue_table())
    assert get_cell_context(st, row=99, col=0) is None
    assert get_cell_context(st, row=0, col=99) is None


# --------------------------------------------------------------------------
# Unit / period context
# --------------------------------------------------------------------------

def test_unit_context_is_table_local():
    st = structure_table(_revenue_table(unit="USD million"))
    assert st.unit == "USD million"
    cell = get_cell_context(st, row=0, col=1)
    assert cell.unit == "USD million"


def test_adjacent_tables_do_not_share_units_or_headers():
    a = structure_table(_revenue_table(unit="USD million"))
    b = structure_table(Table(
        page_no=1, bbox=(0, 200, 200, 300),
        rows=[["", "2024"], ["Growth", "6.5%"]],
        caption="Growth rate", scale_context="",
    ))
    assert a.unit != b.unit
    assert get_cell_context(a, 0, 1).unit != get_cell_context(b, 0, 0).unit


def test_period_header_is_recognized():
    st = structure_table(_revenue_table())
    cell = get_cell_context(st, row=0, col=1)
    assert cell.period_label is not None   # "FY2024" parses as a real period


def test_non_period_header_is_not_forced_into_a_period():
    t = Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["Metric", "Category A", "Category B"],
                                                     ["Revenue", "500", "620"]])
    st = structure_table(t)
    cell = get_cell_context(st, row=0, col=1)
    assert cell.period_label is None
    assert cell.column_header == "Category A"


# --------------------------------------------------------------------------
# Multi-row headers
# --------------------------------------------------------------------------

def test_multi_row_header_is_detected_and_combined():
    t = Table(
        page_no=1, bbox=(0, 0, 10, 10),
        rows=[
            ["", "2024", "", "2025", ""],
            ["", "Actual", "Forecast", "Actual", "Forecast"],
            ["India", "500", "540", "620", "650"],
        ],
    )
    st = structure_table(t)
    assert st.header_row_count == 2
    assert st.column_headers[1] == "2024 Actual"
    assert st.column_headers[2] == "2024 Forecast"
    assert st.column_headers[3] == "2025 Actual"
    cell = get_cell_context(st, row=0, col=1)
    assert cell.row_label == "India" and cell.text == "500"
    assert cell.column_header == "2024 Actual"


def test_single_row_header_stays_single_level_when_shape_does_not_match():
    """Row 1 here is itself numeric-looking data, not a sub-label row — must
    NOT be misread as a second header level."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["", "FY2024", "FY2025"], ["India", "500", "620"], ["US", "300", "350"]])
    st = structure_table(t)
    assert st.header_row_count == 1


# --------------------------------------------------------------------------
# Ambiguity (A6.6) — must never guess
# --------------------------------------------------------------------------

def test_row_with_two_numbers_in_one_cell_is_ambiguous():
    """The real ESOPs shape: a data cell packs two numbers with no column
    to disambiguate which belongs to which plan."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["Plan", "Vested"], ["ESOP", "676,000 - 250,000"]])
    st = structure_table(t)
    cell = get_cell_context(st, row=0, col=1)
    assert cell.ambiguous is True


def test_numeric_first_column_disables_row_label_confidence():
    """If most of column 0's data cells are themselves numeric, this table's
    first column is a DATA column, not a row-label column — assuming
    otherwise would misattribute every value in the row."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["Year", "Revenue"], ["2024", "500"], ["2025", "620"]])
    st = structure_table(t)
    assert st.row_label_confident is False
    assert all(c.ambiguous for row in st.cells for c in row)


def test_find_cells_matching_value_returns_single_unambiguous_match():
    st = structure_table(_revenue_table())
    matches = find_cells_matching_value(st, "620")
    assert len(matches) == 1
    assert matches[0].row_label == "India" and matches[0].column_header == "FY2025"


def test_find_cells_matching_value_excludes_ambiguous_cells():
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["Plan", "Vested"], ["ESOP", "676,000 - 250,000"]])
    st = structure_table(t)
    assert find_cells_matching_value(st, "676,000") == []


# --------------------------------------------------------------------------
# Missing cell / malformed table — graceful degradation, no crash
# --------------------------------------------------------------------------

def test_missing_cell_does_not_crash():
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["", "FY2024", "FY2025"], ["India", None, "620"]])
    st = structure_table(t)
    cell = get_cell_context(st, row=0, col=1)
    assert cell.text == ""


def test_ragged_rows_do_not_crash():
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["", "FY2024", "FY2025"], ["India", "500"], ["US", "300", "350", "extra"]])
    st = structure_table(t)
    assert len(st.cells) == 2


def test_empty_table_does_not_crash():
    t = Table(page_no=1, bbox=(0, 0, 1, 1), rows=[])
    st = structure_table(t)
    assert st.header_row_count == 0
    assert st.cells == []
    assert get_cell_context(st, 0, 0) is None
