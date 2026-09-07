"""Tests for fact_layer/diagnostics.py (A4 — parser diagnostics).

Synthetic Page/Document fixtures for the unit-level behaviour (fast,
deterministic); the real 6-PDF corpus is used only to confirm diagnostics
generate cleanly end-to-end, reusing the same parsed-document cache
test_parse.py already relies on.
"""

import functools
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.diagnostics import (
    _SPARSE_WORD_THRESHOLD,
    compute_document_diagnostics,
    compute_page_diagnostics,
    detect_repeated_edge_lines,
)
from fact_layer.parse import Document, Page, Word, parse_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_parse = functools.lru_cache(maxsize=None)(parse_pdf)


def _make_page(text: str, page_no: int = 1, tables: list = None, image_only: bool = False) -> Page:
    words = []
    offset = 0
    for token in text.split():
        start = text.find(token, offset)
        end = start + len(token)
        words.append(Word(text=token, char_start=start, char_end=end, bbox=(0, 0, 10, 10)))
        offset = end
    return Page(page_no=page_no, text=text, words=words, tables=tables or [], image_only=image_only)


# --------------------------------------------------------------------------
# 13/14 — repeated header / footer detection
# --------------------------------------------------------------------------

def test_repeated_header_is_detected():
    pages = [
        _make_page(f"SUPER COMPANY LIMITED\nAnnual Report 2024-25\nBody text for page {i}.", page_no=i)
        for i in range(1, 6)
    ]
    doc = Document(doc_id="d", filename="f.pdf", n_pages=len(pages), pages=pages)
    headers, footers = detect_repeated_edge_lines(doc)
    assert any("SUPER COMPANY LIMITED" in h for h in headers)


def test_repeated_footer_with_varying_page_number_is_detected():
    """Page numbers differ per page ("Page 1", "Page 2", ...) but the
    NORMALIZED pattern (digits collapsed) must still be recognised as one
    recurring footer, matching the brief's own "Page 47" example."""
    pages = [
        _make_page(f"Body text for page {i}.\nSUPER COMPANY LIMITED\nPage {i}", page_no=i)
        for i in range(1, 6)
    ]
    doc = Document(doc_id="d", filename="f.pdf", n_pages=len(pages), pages=pages)
    headers, footers = detect_repeated_edge_lines(doc)
    assert any("Page" in f for f in footers)


# --------------------------------------------------------------------------
# 15 — non-repeated top text is not a false-positive header
# --------------------------------------------------------------------------

def test_non_repeated_top_text_is_not_classified_as_header():
    """Five pages with genuinely distinct opening lines — no shared template,
    not even one differing only by a trailing digit — must never collapse
    into a false "repeated header"."""
    openings = [
        "Introduction to the business segment overview.",
        "Summary of key risk factors identified this year.",
        "Notes on related party transactions and disclosures.",
        "Overview of corporate governance practices adopted.",
        "Discussion of liquidity and capital resource management.",
    ]
    pages = [_make_page(opening, page_no=i) for i, opening in enumerate(openings, start=1)]
    doc = Document(doc_id="d", filename="f.pdf", n_pages=len(pages), pages=pages)
    headers, _ = detect_repeated_edge_lines(doc)
    assert headers == []


def test_short_document_below_minimum_page_count_reports_no_headers():
    """Two pages sharing a line should NOT be called "repeated" — the
    absolute floor (_REPEAT_MIN_PAGES) guards against a coincidental match
    in a short document being over-interpreted as structural noise."""
    pages = [_make_page("Shared Title\nBody one.", page_no=1),
             _make_page("Shared Title\nBody two.", page_no=2)]
    doc = Document(doc_id="d", filename="f.pdf", n_pages=2, pages=pages)
    headers, _ = detect_repeated_edge_lines(doc)
    assert headers == []


# --------------------------------------------------------------------------
# 16/17 — image-only vs. sparse-but-real pages
# --------------------------------------------------------------------------

def test_image_only_page_is_flagged():
    page = _make_page("", page_no=1, image_only=True)
    diag = compute_page_diagnostics(page)
    assert diag.image_only is True
    assert diag.word_count == 0
    assert any("image-only" in w or "no extractable text" in w for w in diag.warnings)


def test_sparse_but_real_page_is_not_image_only():
    """A handful of real words (e.g. a slide divider: 'Section 2 — Financials')
    must be flagged sparse, never misclassified as image_only — that field's
    meaning (zero extractable text) must stay exactly what parse.py sets."""
    text = "Section Two Financials"   # 3 words, well under the sparse threshold
    assert len(text.split()) < _SPARSE_WORD_THRESHOLD
    page = _make_page(text, page_no=1, image_only=False)
    diag = compute_page_diagnostics(page)
    assert diag.image_only is False
    assert any("sparse" in w for w in diag.warnings)


def test_normal_dense_page_has_no_warnings():
    text = " ".join(f"word{i}" for i in range(60))
    page = _make_page(text, page_no=1)
    diag = compute_page_diagnostics(page)
    assert diag.warnings == []


# --------------------------------------------------------------------------
# 18 — parser warnings never reject facts (different mechanisms entirely)
# --------------------------------------------------------------------------

def test_sparse_page_warning_does_not_block_fact_acceptance(tmp_path):
    """A page diagnostics flags as sparse must still extract and verify a
    fact normally through the real extract.py path — a parser WARNING and a
    fact REJECTION are different mechanisms, never coupled."""
    from fact_layer.extract import verify_and_build_fact

    text = "Revenue was Rs. 100 Cr."   # short page: sparse by word count
    page = _make_page(text, page_no=1)
    assert compute_page_diagnostics(page).warnings   # confirm it IS flagged sparse

    raw_fact = {
        "subject_raw": "Acme", "measure_raw": "Revenue", "value_raw": "100",
        "period_raw": None, "scope_raw": None, "issuer_raw": None,
        "modality": "asserted", "verbatim_quote": "Revenue was Rs. 100 Cr.",
    }
    fact = verify_and_build_fact(raw_fact, [page], "doc1", "test.pdf", {}, "",
                                  str(tmp_path / "rejected.jsonl"))
    assert fact is not None, "a sparse-page warning must not cause fact rejection"


def test_diagnostics_module_does_not_import_extraction_or_rejection_logic():
    """Architectural guard: diagnostics.py has no import of extract.py — the
    reverse dependency (extract.py -> diagnostics.py) would be fine, but
    diagnostics must never reach into rejection logic to decide anything."""
    import fact_layer.diagnostics as diagnostics_module
    assert not hasattr(diagnostics_module, "extract")
    assert not hasattr(diagnostics_module, "verify_and_build_fact")


# --------------------------------------------------------------------------
# 8/document-level aggregation
# --------------------------------------------------------------------------

def test_document_diagnostics_page_counts_are_consistent():
    pages = [
        _make_page(" ".join(f"word{i}" for i in range(15)), page_no=1),   # 15 words: not sparse
        _make_page("", page_no=2, image_only=True),
        _make_page("Two words", page_no=3),                               # 2 words: sparse
    ]
    doc = Document(doc_id="d", filename="f.pdf", n_pages=3, pages=pages)
    d = compute_document_diagnostics(doc)
    assert d.total_pages == 3
    assert d.image_only_pages == 1
    assert d.text_pages == 2
    assert d.sparse_pages == 1   # page 3, "Two words"
    assert len(d.pages) == 3


# --------------------------------------------------------------------------
# 19 — real corpus diagnostics generate successfully
# --------------------------------------------------------------------------

def test_real_corpus_diagnostics_generate_without_error():
    pdf_paths = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
    assert pdf_paths, "expected the real starter-dataset PDFs to be present"
    for path in pdf_paths:
        doc = _parse(path)
        d = compute_document_diagnostics(doc)
        assert d.total_pages == doc.n_pages
        assert d.text_pages + d.image_only_pages == d.total_pages
        assert d.tables_detected == sum(len(p.tables) for p in doc.pages)


def test_imf_cover_page_reflected_in_real_diagnostics():
    """Cross-check against the existing test_parse.py invariant
    (test_imf_cover_page_is_image_only) from the diagnostics side."""
    imf_path = next(
        p for p in sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
        if "imf" in p.lower()
    )
    doc = _parse(imf_path)
    d = compute_document_diagnostics(doc)
    assert d.image_only_pages >= 1
