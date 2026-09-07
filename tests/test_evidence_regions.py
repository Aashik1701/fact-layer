"""Tests for A8 — region/cell-level evidence.

Exercises the full verify_and_build_fact() path with a table present in
the chunk, confirming: original quote stays canonical, cell/table metadata
populates only when confidently attributable, and old persisted Evidence
records (without these fields) still load safely.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.extract import verify_and_build_fact
from fact_layer.models import Evidence
from fact_layer.parse import Page, Table, Word
from fact_layer.store import _evidence_from_dict, _evidence_to_dict


def _page_with_table():
    """A page whose text contains a value that also appears, with row/column
    structure, in a Table on the same page — the shape A8 attribution needs."""
    text = "India revenue for FY2025 was 620 as stated in the segment table below."
    words = []
    offset = 0
    for token in text.split():
        start = text.find(token, offset)
        end = start + len(token)
        words.append(Word(text=token, char_start=start, char_end=end, bbox=(50, 100, 50 + len(token) * 6, 110)))
        offset = end
    # place the table bbox so it covers the word "620"'s position (50,100)-ish region
    idx = text.index("620")
    table = Table(
        page_no=1, bbox=(0, 90, 200, 120),
        rows=[["", "FY2024", "FY2025"], ["India", "500", "620"], ["US", "300", "350"]],
        cell_bboxes=[
            [(0, 90, 10, 100), (10, 90, 20, 100), (20, 90, 30, 100)],
            [(0, 100, 10, 110), (10, 100, 20, 110), (20, 100, 30, 110)],
            [(0, 110, 10, 120), (10, 110, 20, 120), (20, 110, 30, 120)],
        ],
        caption="Revenue", scale_context="USD million",
    )
    return Page(page_no=1, text=text, words=words, tables=[table]), table, idx


def _find_word_bbox_for(page, needle):
    for w in page.words:
        if w.text.strip(",.") == needle:
            return w.bbox
    return None


def test_original_quote_stays_canonical_when_table_attribution_succeeds(tmp_path):
    page, table, _ = _page_with_table()
    raw_fact = {
        "subject_raw": "India", "measure_raw": "Revenue", "value_raw": "620",
        "period_raw": "FY2025", "scope_raw": None, "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "India revenue for FY2025 was 620",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"), tables=[table])
    assert fact is not None
    assert fact.evidence.verbatim_quote == "India revenue for FY2025 was 620"
    assert fact.evidence.verbatim_quote in page.text


def test_table_metadata_populated_when_bbox_falls_inside_table(tmp_path, monkeypatch):
    """Force bbox_for_span() to return a bbox inside the table's bbox (the
    word-bbox layout in this synthetic fixture is approximate; this test
    isolates the attribution behaviour itself rather than fighting pixel-
    perfect coordinates)."""
    page, table, _ = _page_with_table()

    def fake_bbox_for_span(self, start, end):
        return (5.0, 105.0, 15.0, 108.0)   # inside table.bbox=(0,90,200,120)

    monkeypatch.setattr(Page, "bbox_for_span", fake_bbox_for_span)

    raw_fact = {
        "subject_raw": "India", "measure_raw": "Revenue", "value_raw": "620",
        "period_raw": "FY2025", "scope_raw": None, "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "India revenue for FY2025 was 620",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"), tables=[table])
    assert fact is not None
    assert fact.evidence.table_id is not None
    assert fact.evidence.row_label == "India"
    assert fact.evidence.column_header == "FY2025"
    assert fact.evidence.unit_context == "USD million"
    assert fact.evidence.cell_bbox == (20, 100, 30, 110)
    assert fact.value_verification == "verified_with_context"


def test_no_table_attribution_when_bbox_outside_any_table(tmp_path):
    page, table, _ = _page_with_table()
    # move the table far away so the fact's bbox (from real word positions,
    # which sit around top=100-110) never falls inside it
    table.bbox = (0, 500, 200, 600)

    raw_fact = {
        "subject_raw": "India", "measure_raw": "Revenue", "value_raw": "620",
        "period_raw": "FY2025", "scope_raw": None, "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "India revenue for FY2025 was 620",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"), tables=[table])
    assert fact is not None
    assert fact.evidence.table_id is None
    assert fact.evidence.row_label is None
    assert fact.value_verification == "verified"   # plain VERIFIED, not upgraded


def test_no_tables_argument_behaves_exactly_as_before(tmp_path):
    """Callers that don't pass `tables` (every pre-A6/A7/A8 call site) must
    see identical behaviour — table_id/row_label etc. simply stay None."""
    page, table, _ = _page_with_table()
    raw_fact = {
        "subject_raw": "India", "measure_raw": "Revenue", "value_raw": "620",
        "period_raw": "FY2025", "scope_raw": None, "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "India revenue for FY2025 was 620",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"))   # no tables=
    assert fact is not None
    assert fact.evidence.table_id is None
    assert fact.value_verification == "verified"


# --------------------------------------------------------------------------
# Backward compatibility — old persisted Evidence dicts load safely
# --------------------------------------------------------------------------

def test_old_evidence_dict_without_new_fields_deserializes_safely():
    old_dict = {
        "doc_id": "d1", "page": 1, "char_start": 0, "char_end": 5,
        "verbatim_quote": "hello", "bbox": None, "extractor": "llm", "verified": True,
    }
    ev = _evidence_from_dict(old_dict)
    assert ev.verbatim_quote == "hello"
    assert ev.table_id is None
    assert ev.row_label is None
    assert ev.column_header is None
    assert ev.unit_context == ""


def test_evidence_round_trips_new_fields():
    ev = Evidence(doc_id="d1", page=1, char_start=0, char_end=3, verbatim_quote="620",
                  table_id="d1_p1_abcd1234", row_index=0, column_index=2,
                  cell_bbox=(1.0, 2.0, 3.0, 4.0), row_label="India",
                  column_header="FY2025", unit_context="USD million")
    restored = _evidence_from_dict(_evidence_to_dict(ev))
    assert restored == ev
