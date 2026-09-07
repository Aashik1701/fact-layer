"""Tests for A7 — context-aware value verification.

Exercises value_verify.verify_value()'s optional `cell_context` parameter
(fed from table_structure.py's CellContext) and confirms it can only ever
UPGRADE an already-agreeing numeric check, never resolve an ambiguity the
quote-level check alone could not.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.normalize import parse_quantity
from fact_layer.parse import Table
from fact_layer.table_structure import find_cells_matching_value, structure_table
from fact_layer.value_verify import ValueVerificationStatus, verify_value


def _revenue_table():
    return Table(
        page_no=1, bbox=(0, 0, 200, 100),
        rows=[["", "FY2024", "FY2025"], ["India", "500", "620"], ["US", "300", "350"]],
        caption="Revenue", scale_context="USD million",
    )


# --------------------------------------------------------------------------
# Direct numeric verification (no cell_context) — baseline, unaffected
# --------------------------------------------------------------------------

def test_verify_value_without_cell_context_is_unaffected():
    q = parse_quantity("620", "USD million")
    r = verify_value("620", "India revenue for FY2025 was 620", q, "USD million")
    assert r.status is ValueVerificationStatus.VERIFIED


# --------------------------------------------------------------------------
# Table-context verification — upgrades a confirmed match
# --------------------------------------------------------------------------

def test_confident_table_cell_upgrades_to_verified_with_context():
    st = structure_table(_revenue_table())
    matches = find_cells_matching_value(st, "620")
    assert len(matches) == 1
    cell = matches[0]

    q = parse_quantity("620", "USD million")
    r = verify_value("620", "India revenue for FY2025 was 620", q, "USD million", cell_context=cell)

    assert r.status is ValueVerificationStatus.VERIFIED_WITH_CONTEXT
    assert r.row_label == "India"
    assert r.column_header == "FY2025"
    assert r.unit_context == "USD million"


def test_table_context_never_resolves_a_quote_level_mismatch():
    """A confident cell match cannot rescue a value that the QUOTE itself
    disagrees with — table context only strengthens an existing agreement,
    it never overrides disagreement."""
    st = structure_table(_revenue_table())
    matches = find_cells_matching_value(st, "620")
    cell = matches[0]

    q = parse_quantity("999", "USD million")   # value_raw disagrees with both quote and cell
    r = verify_value("999", "India revenue for FY2025 was 999", q, "USD million", cell_context=cell)
    assert r.status is not ValueVerificationStatus.VERIFIED_WITH_CONTEXT


def test_ambiguous_cell_context_never_upgrades():
    """The real ESOPs shape: even if somehow passed in, an ambiguous
    CellContext must never produce VERIFIED_WITH_CONTEXT."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10),
              rows=[["Plan", "Vested"], ["ESOP", "676,000 - 250,000"]])
    st = structure_table(t)
    ambiguous_cell = st.cells[0][1]
    assert ambiguous_cell.ambiguous is True

    q = parse_quantity("676,000", "")
    r = verify_value("676,000", "ESOPs vested - 676,000 - 250,000", q, "", cell_context=ambiguous_cell)
    assert r.status is not ValueVerificationStatus.VERIFIED_WITH_CONTEXT


def test_cell_context_with_no_row_or_column_label_does_not_upgrade():
    """A cell match with neither a row label nor a column header carries no
    additional context worth claiming — stay at plain VERIFIED rather than
    implying confirmation that isn't really there."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["620"]])
    st = structure_table(t)
    # single-row, single-column table: header_row_count treats the only row
    # as data (len(rows) < 3 -> header_row_count=1 actually claims row 0 as
    # header) — force a genuinely label-less/header-less cell directly:
    from fact_layer.table_structure import CellContext
    bare_cell = CellContext(text="620", row_index=0, column_index=0, bbox=None,
                             row_label=None, column_header=None, unit="",
                             period_label=None, ambiguous=False, source_page=1)
    q = parse_quantity("620", "")
    r = verify_value("620", "the figure was 620", q, "", cell_context=bare_cell)
    assert r.status is ValueVerificationStatus.VERIFIED


# --------------------------------------------------------------------------
# A7.4 — existing A1 behaviors must not regress
# --------------------------------------------------------------------------

def test_unicode_minus_still_handled_with_cell_context_absent():
    q = parse_quantity("−0.4 percent", "")
    r = verify_value("−0.4 percent", "projected at −0.4 percent of GDP", q, "")
    assert r.status is ValueVerificationStatus.VERIFIED


def test_multi_candidate_ambiguity_still_unverified():
    q = parse_quantity("120.4", "")
    r = verify_value("120.4", "Revenue 120.4 145.9 132.1", q, "")
    assert r.status is ValueVerificationStatus.UNVERIFIED
