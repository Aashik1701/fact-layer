"""Tests for the lightweight table-structure context added to fact_layer.Table
(A3 — structured layout/table context). Synthetic Table fixtures are used
here deliberately (unlike test_parse.py, which is real-PDF-only by
convention) since Table's formatting/accessor logic is pure and its
correctness doesn't depend on any particular source document.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.parse import Page, Table, Word
from fact_layer.value_verify import ValueVerificationStatus, verify_value
from fact_layer.normalize import parse_quantity


def _revenue_table() -> Table:
    return Table(
        page_no=1,
        bbox=(0, 0, 200, 100),
        rows=[
            ["", "FY2024", "FY2025"],
            ["India", "500", "620"],
            ["US", "300", "350"],
        ],
        cell_bboxes=[
            [(0, 0, 10, 10), (10, 0, 20, 10), (20, 0, 30, 10)],
            [(0, 10, 10, 20), (10, 10, 20, 20), (20, 10, 30, 20)],
            [(0, 20, 10, 30), (10, 20, 20, 30), (20, 20, 30, 30)],
        ],
        caption="Revenue (₹ crore)",
        scale_context="₹ crore",
    )


# --------------------------------------------------------------------------
# 3/4/5 — rows, column headers, and row labels are preserved
# --------------------------------------------------------------------------

def test_header_row_is_the_first_row():
    t = _revenue_table()
    assert t.header_row() == ["", "FY2024", "FY2025"]


def test_row_label_is_first_cell_of_each_data_row():
    t = _revenue_table()
    assert t.row_label(1) == "India"
    assert t.row_label(2) == "US"


def test_header_row_on_empty_table_is_none():
    t = Table(page_no=1, bbox=(0, 0, 1, 1), rows=[])
    assert t.header_row() is None
    assert t.row_label(0) == ""


def test_to_text_block_preserves_every_cell_value():
    t = _revenue_table()
    block = t.to_text_block()
    for expected in ("FY2024", "FY2025", "India", "500", "620", "US", "300", "350"):
        assert expected in block, f"{expected!r} missing from to_text_block() output"


def test_to_text_block_is_byte_for_byte_unchanged_by_header_row_accessors():
    """header_row()/row_label() are additive metadata, never mixed into the
    LLM-facing string — to_text_block() must stay EXACTLY the pre-A3 format
    (caption, [scale], pipe-joined rows, no "headers:"/"row:" prefixes),
    because that string is hashed verbatim into the replay-cache key
    (llm.py). A richer label was tried and reverted for exactly this
    reason; this test pins the reversion down so it cannot silently
    regress."""
    t = _revenue_table()
    block = t.to_text_block()
    lines = block.splitlines()
    assert lines[0] == "Revenue (₹ crore)"
    assert lines[1] == "[₹ crore]"
    assert not any(l.startswith("headers:") or l.startswith("row:") for l in lines)
    assert " | FY2024 | FY2025" in lines
    assert "India | 500 | 620" in lines[3]


# --------------------------------------------------------------------------
# 6 — cell bounding boxes preserved alongside cell text
# --------------------------------------------------------------------------

def test_cell_bboxes_shape_matches_rows():
    t = _revenue_table()
    assert len(t.cell_bboxes) == len(t.rows)
    for bbox_row, text_row in zip(t.cell_bboxes, t.rows):
        assert len(bbox_row) == len(text_row)
    # spot-check one real bbox survives untouched
    assert t.cell_bboxes[1][2] == (20, 10, 30, 20)   # India's FY2025 cell


# --------------------------------------------------------------------------
# 7/8 — unit/scale context is table-local, never shared across tables
# --------------------------------------------------------------------------

def test_unit_context_detected_for_simple_table():
    t = _revenue_table()
    assert t.scale_context == "₹ crore"
    assert t.scale_context in t.to_text_block()


def test_adjacent_tables_do_not_share_units():
    table_a = Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["India", "500"]],
                     caption="Revenue (₹ crore)", scale_context="₹ crore")
    table_b = Table(page_no=1, bbox=(0, 50, 10, 60), rows=[["India", "6.0"]],
                     caption="Growth (USD million)", scale_context="USD million")
    assert table_a.scale_context != table_b.scale_context
    assert table_a.scale_context not in table_b.to_text_block()
    assert table_b.scale_context not in table_a.to_text_block()


def test_table_without_confident_unit_leaves_scale_context_unknown():
    """No unit phrase in caption or first rows -> scale_context stays empty
    rather than inheriting from anywhere else — conservative by construction
    (detect_scale_context() is only ever called with this table's own
    caption+first-rows text, never another table's or the document's)."""
    t = Table(page_no=1, bbox=(0, 0, 10, 10), rows=[["India", "500"]], caption="Headcount")
    assert t.scale_context == ""


# --------------------------------------------------------------------------
# 9 — multi-number ambiguity survives table-context formatting (A3 + A1)
# --------------------------------------------------------------------------

def test_ambiguous_two_plan_row_stays_unverified_through_table_formatting():
    """The real ESOPs row shape: one label, two numbers, no column
    attribution recoverable from the row text alone. Rendering it through
    Table.to_text_block() must not somehow disambiguate it — value_verify
    remains the sole, unfooled authority."""
    t = Table(
        page_no=43, bbox=(0, 0, 10, 10),
        rows=[["Plan", "Vested"], ["ESOP", "676,000 - 250,000"]],
        caption="ESOP Vesting",
    )
    block = t.to_text_block()
    assert "676,000" in block and "250,000" in block

    quote = "No. of ESOPs vested as on - 676,000 - 250,000"
    q = parse_quantity("676,000", "")
    result = verify_value("676,000", quote, q, "")
    assert result.status is ValueVerificationStatus.UNVERIFIED


# --------------------------------------------------------------------------
# 10 — evidence stays anchored to original page text, never to to_text_block()
# --------------------------------------------------------------------------

def test_evidence_quote_is_original_page_text_not_table_block(tmp_path):
    """The new "headers:"/"row:" labels Table.to_text_block() emits must
    never appear in evidence.verbatim_quote — evidence is verified against
    Page.text exactly as pdfplumber produced it, a completely separate path
    from the LLM-facing table formatting."""
    from fact_layer.extract import verify_and_build_fact

    page_text = "India revenue for FY2025 was 620 as reported in the segment table."
    words = []
    offset = 0
    for token in page_text.split():
        start = page_text.find(token, offset)
        end = start + len(token)
        words.append(Word(text=token, char_start=start, char_end=end, bbox=(0, 0, 10, 10)))
        offset = end
    page = Page(page_no=1, text=page_text, words=words, tables=[_revenue_table()])

    raw_fact = {
        "subject_raw": "India", "measure_raw": "Revenue", "value_raw": "620",
        "period_raw": "FY2025", "scope_raw": None, "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "India revenue for FY2025 was 620",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"))
    assert fact is not None
    assert fact.evidence.verbatim_quote == "India revenue for FY2025 was 620"
    assert "headers:" not in fact.evidence.verbatim_quote
    assert "row:" not in fact.evidence.verbatim_quote
    assert fact.evidence.verbatim_quote in page_text
