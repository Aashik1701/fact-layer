"""Tests for fact_layer/extract.py.

All tests run offline using RECORDED cache fixtures (LLM_MODE=replay, no
network), enforced by monkeypatching httpx.post to raise on any call attempt.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from fact_layer import llm
from fact_layer.extract import (
    ExtractionStats,
    PayloadChunk,
    RejectedFact,
    _append_rejected,
    _build_report,
    _find_exact_span,
    _fuzzy_find_span,
    _normalise_whitespace,
    _split_batch_into_chunks,
    _split_page_into_chunks,
    _split_prose_by_chars,
    _split_table_block_by_rows,
    extract_document_defaults,
    verify_and_build_fact,
)
from fact_layer.models import Quantity, Scope, ValueKind
from fact_layer.parse import Page, Table, Word


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted in tests")


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Isolate LLM cache and rejected-facts output for each test."""
    monkeypatch.setattr(llm, "_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    llm._stats.update(calls=0, cache_hits=0, network_calls=0,
                      estimated_input_tokens=0, repair_attempts=0)
    yield


def _make_page(text: str, page_no: int = 1, tables: list = None) -> Page:
    """Build a minimal Page with word-level offsets for testing."""
    words = []
    offset = 0
    for token in text.split():
        start = text.find(token, offset)
        end = start + len(token)
        words.append(Word(text=token, char_start=start, char_end=end,
                          bbox=(0, 0, 10, 10)))
        offset = end
    return Page(page_no=page_no, text=text, words=words, tables=tables or [])


# --------------------------------------------------------------------------
# PART 0 — Payload guard
# --------------------------------------------------------------------------

def test_payload_guard_splits_oversized_page():
    """A page exceeding 8000 chars must be split into multiple chunks."""
    big_text = "word " * 2000   # ~10,000 chars
    big_table = Table(page_no=1, bbox=(0, 0, 100, 100),
                      rows=[["cell"] * 10] * 20, caption="Big Table",
                      scale_context="in crore")
    page = _make_page(big_text, tables=[big_table])
    chunks = _split_page_into_chunks(page, cap=8000)
    assert len(chunks) > 1, "oversized page should be split into multiple chunks"
    types = {c.prompt_type for c in chunks}
    assert "prose" in types or "table" in types


def test_payload_guard_keeps_small_page_combined():
    """A page under 8000 chars stays as a single combined chunk."""
    small_text = "Revenue was Rs. 120.4 Cr for FY2024."
    page = _make_page(small_text)
    chunks = _split_page_into_chunks(page, cap=8000)
    assert len(chunks) == 1
    assert chunks[0].prompt_type == "combined"


def test_payload_guard_never_truncates_oversized_prose():
    """Prose that alone exceeds the cap must be split into further prose
    chunks whose concatenation recovers every word — never sliced off."""
    words = [f"word{i}" for i in range(500)]
    big_text = " ".join(words)   # no tables, prose alone exceeds a small cap
    page = _make_page(big_text)
    chunks = _split_page_into_chunks(page, cap=200)

    assert len(chunks) > 1
    assert all(c.prompt_type == "prose" for c in chunks)
    assert all(len(c.content) <= 200 for c in chunks)

    recovered_words = " ".join(c.content for c in chunks).split()
    assert recovered_words == words, "no word may be dropped by payload splitting"


def test_payload_guard_never_truncates_oversized_single_table():
    """A single table whose own block exceeds the cap is split by row, not
    truncated — every row must survive across the resulting chunks."""
    rows = [[f"r{i}c1", f"r{i}c2", f"r{i}c3"] for i in range(200)]
    big_table = Table(page_no=1, bbox=(0, 0, 100, 100), rows=rows,
                      caption="Big Table", scale_context="in crore")
    page = _make_page("short page text", tables=[big_table])
    chunks = _split_page_into_chunks(page, cap=500)

    table_chunks = [c for c in chunks if c.prompt_type == "table"]
    assert len(table_chunks) > 1, "a single oversized table must be split into multiple pieces"
    for c in table_chunks:
        assert len(c.content) <= 500

    # Every row's distinguishing cell must appear somewhere across the pieces.
    all_content = "\n".join(c.content for c in table_chunks)
    for i in range(200):
        assert f"r{i}c1" in all_content, f"row {i} was dropped by table splitting"


# --------------------------------------------------------------------------
# PART 0 — batching (triage.batch_pages must actually reduce call count)
# --------------------------------------------------------------------------

def test_batch_of_small_pages_becomes_one_combined_chunk():
    """Several small pages that together fit under the cap must produce ONE
    chunk, not one call per page — this is the point of triage batching."""
    p1 = _make_page("Revenue was Rs. 100 Cr in FY2023.", page_no=1)
    p2 = _make_page("Revenue was Rs. 120 Cr in FY2024.", page_no=2)
    p3 = _make_page("Profit was Rs. 30 Cr in FY2024.", page_no=3)

    chunks = _split_batch_into_chunks([p1, p2, p3], cap=8000)

    assert len(chunks) == 1, "small batched pages should collapse into a single call"
    assert set(p.page_no for p in chunks[0].pages) == {1, 2, 3}
    assert "PAGE 1" in chunks[0].content and "PAGE 2" in chunks[0].content


def test_batch_that_does_not_fit_falls_back_to_per_page():
    """If a batch's combined content exceeds the cap, fall back to
    per-page splitting rather than truncating the batch."""
    p1 = _make_page("word " * 400, page_no=1)   # ~2000 chars
    p2 = _make_page("word " * 400, page_no=2)

    chunks = _split_batch_into_chunks([p1, p2], cap=1000)

    assert len(chunks) >= 2
    covered_pages = {pn for c in chunks for pn in (p.page_no for p in c.pages)}
    assert covered_pages == {1, 2}


def test_verify_finds_quote_on_second_page_of_batch():
    """A batched call's verbatim_quote may come from any page in the batch —
    the verifier must find it on whichever one actually contains it and
    attribute evidence to that page, not just the first."""
    p1 = _make_page("Nothing relevant here at all.", page_no=5)
    p2 = _make_page("Revenue from operations was Rs. 8,141.71 Cr for FY2024.", page_no=6)

    raw_fact = {
        "subject_raw": "Company",
        "measure_raw": "Revenue from operations",
        "value_raw": "8,141.71",
        "period_raw": "FY2024",
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Revenue from operations was Rs. 8,141.71 Cr for FY2024.",
    }

    fact = verify_and_build_fact(raw_fact, [p1, p2], "doc1", "test.pdf", {}, "")

    assert fact is not None
    assert fact.evidence.page == 6, "evidence must point at the page that actually contains the quote"


# --------------------------------------------------------------------------
# PART 1 — Document metadata
# --------------------------------------------------------------------------

def test_document_defaults_inherited(tmp_path):
    """Facts inherit document-level defaults when per-fact fields are null."""
    doc_defaults = {
        "issuer": "Delhivery Limited",
        "reporting_entity": "Delhivery Limited",
        "document_type": "annual_report",
        "default_currency": "INR",
        "default_scale": "crore",
        "default_scope": "consolidated",
        "default_period": "FY2023-24",
    }

    page_text = "Revenue from operations was 8,141.71 for the year ended March 31, 2024."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Delhivery",
        "measure_raw": "Revenue from operations",
        "value_raw": "8,141.71",
        "period_raw": None,         # null — should inherit default_period
        "scope_raw": None,          # null — should inherit default_scope
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Revenue from operations was 8,141.71",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 doc_defaults, "crore", rejected_path)

    assert fact is not None
    # Scope should be inherited from doc_defaults
    assert fact.qualifiers.scope == Scope.CONSOLIDATED
    # Period should be inherited
    assert fact.qualifiers.period is not None
    assert fact.qualifiers.period.label == "FY2023-24"


def test_per_fact_overrides_win(tmp_path):
    """Per-fact scope_raw overrides the document default_scope."""
    doc_defaults = {
        "issuer": "Delhivery Limited",
        "reporting_entity": "Delhivery Limited",
        "document_type": "annual_report",
        "default_currency": "INR",
        "default_scale": "crore",
        "default_scope": "consolidated",
        "default_period": "FY2023-24",
    }

    page_text = "Standalone revenue was 5,200.00 for the year ended March 31, 2024."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Delhivery",
        "measure_raw": "Revenue",
        "value_raw": "5,200.00",
        "period_raw": "year ended March 31, 2024",
        "scope_raw": "Standalone",   # overrides "consolidated" default
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Standalone revenue was 5,200.00",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 doc_defaults, "crore", rejected_path)

    assert fact is not None
    assert fact.qualifiers.scope == Scope.STANDALONE   # override wins


# --------------------------------------------------------------------------
# PART 3 — Span verifier
# --------------------------------------------------------------------------

def test_known_good_quote_verifies(tmp_path):
    """A quote that exactly matches page text verifies with correct offsets."""
    page_text = "Revenue from operations was Rs. 8,141.71 Cr for FY2024."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Company",
        "measure_raw": "Revenue from operations",
        "value_raw": "8,141.71",
        "period_raw": "FY2024",
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Revenue from operations was Rs. 8,141.71 Cr for FY2024.",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {}, "", rejected_path)

    assert fact is not None
    assert fact.evidence is not None
    assert fact.evidence.verified is True
    assert fact.evidence.char_start >= 0
    assert fact.evidence.char_end > fact.evidence.char_start


def test_corrupted_quote_rejected(tmp_path):
    """A deliberately corrupted quote is rejected and lands in rejected_facts."""
    page_text = "Revenue from operations was Rs. 8,141.71 Cr for FY2024."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Company",
        "measure_raw": "Revenue",
        "value_raw": "8,141.71",
        "period_raw": "FY2024",
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "TOTALLY DIFFERENT TEXT THAT DOES NOT EXIST IN THE PAGE AT ALL",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {}, "", rejected_path)

    assert fact is None

    # Verify it landed in rejected_facts.jsonl
    assert os.path.exists(rejected_path)
    with open(rejected_path, "r") as f:
        lines = f.readlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["reason"] == "quote_not_found"
    assert record["doc_id"] == "doc1"


def test_value_raw_never_transformed(tmp_path):
    """value_raw from LLM output is passed as-is to parse_quantity,
    never numerically transformed by the extraction path."""
    page_text = "The total revenue was 120.4 crore for the period."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Company",
        "measure_raw": "Total Revenue",
        "value_raw": "120.4",          # raw literal
        "period_raw": None,
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "total revenue was 120.4 crore",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {"default_scale": "crore"}, "", rejected_path)

    assert fact is not None
    assert isinstance(fact.value, Quantity)
    # value_raw "120.4" with context "crore" → parse_quantity normalises
    # but the RAW literal stored must be "120.4"
    assert fact.value.raw == "120.4"


def test_fuzzy_snap_works(tmp_path):
    """A quote with minor whitespace differences still verifies via fuzzy match."""
    page_text = "Revenue  from   operations   was  Rs.  8,141.71  Cr  for  FY2024."
    page = _make_page(page_text)

    # The quote has normalised whitespace (single spaces) — differs from original
    raw_fact = {
        "subject_raw": "Company",
        "measure_raw": "Revenue from operations",
        "value_raw": "8,141.71",
        "period_raw": "FY2024",
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Revenue from operations was Rs. 8,141.71 Cr for FY2024.",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {}, "", rejected_path)

    assert fact is not None
    assert fact.evidence.verified is True


def test_no_subject_rejected(tmp_path):
    """A raw fact with empty subject_raw is rejected with reason 'no_subject'."""
    page_text = "Revenue was Rs. 100 Cr."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "",
        "measure_raw": "Revenue",
        "value_raw": "100",
        "period_raw": None,
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Revenue was Rs. 100 Cr.",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {}, "", rejected_path)

    assert fact is None
    with open(rejected_path, "r") as f:
        record = json.loads(f.readline())
    assert record["reason"] == "no_subject"


def test_no_measure_rejected(tmp_path):
    """A raw fact with empty measure_raw is rejected with reason 'no_measure'."""
    page_text = "Acme Corp reported Rs. 100 Cr."
    page = _make_page(page_text)

    raw_fact = {
        "subject_raw": "Acme Corp",
        "measure_raw": "",
        "value_raw": "100",
        "period_raw": None,
        "scope_raw": None,
        "issuer_raw": None,
        "modality": "asserted",
        "verbatim_quote": "Acme Corp reported Rs. 100 Cr.",
    }

    rejected_path = str(tmp_path / "rejected.jsonl")
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf",
                                 {}, "", rejected_path)

    assert fact is None
    with open(rejected_path, "r") as f:
        record = json.loads(f.readline())
    assert record["reason"] == "no_measure"


# --------------------------------------------------------------------------
# Whitespace normalisation
# --------------------------------------------------------------------------

def test_normalise_whitespace():
    assert _normalise_whitespace("  hello   world  \n foo  ") == "hello world foo"
    assert _normalise_whitespace("no\textra\nspaces") == "no extra spaces"


def test_exact_span_finder():
    text = "Revenue was Rs. 120.4 Cr for FY2024."
    text_norm = _normalise_whitespace(text)
    quote_norm = "Rs. 120.4 Cr"
    result = _find_exact_span(quote_norm, text, text_norm)
    assert result is not None
    start, end = result
    assert "120.4" in text[start:end]


# --------------------------------------------------------------------------
# PART 4 — report metrics must not hardcode the metadata-call count
# --------------------------------------------------------------------------

def test_report_uses_actual_metadata_call_count_not_hardcoded_six():
    """_build_report's llm_calls must reflect the metadata_calls actually
    passed in, not a hardcoded assumption of exactly 6 documents."""
    stats = ExtractionStats(doc_id="d1", doc_filename="only-one-doc.pdf",
                            proposed=3, verified=3, llm_calls=2)

    report_one_doc = _build_report([stats], 3, {}, {}, metadata_calls=1)
    assert report_one_doc["summary"]["llm_calls"] == 3   # 2 extraction + 1 metadata
    assert report_one_doc["summary"]["metadata_calls"] == 1

    report_no_calls = _build_report([], 0, {}, {}, metadata_calls=0)
    assert report_no_calls["summary"]["llm_calls"] == 0   # NOT 6, as the old hardcode would give
