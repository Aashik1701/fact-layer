"""Parser tests run against all six real starter-dataset PDFs.

No synthetic fixtures: milestone 1's contract only means something if it holds
on the actual documents the rest of the pipeline will ingest.
"""

import functools
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.parse import parse_pdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_PATHS = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))

# Parsing all 6 PDFs takes real time (pdfplumber table-finding is not cheap).
# Each real PDF should only be parsed once for the whole module, not once per
# assertion about it.
_parse = functools.lru_cache(maxsize=None)(parse_pdf)


def test_dataset_has_six_pdfs():
    assert len(PDF_PATHS) == 6


def test_every_pdf_parses():
    for path in PDF_PATHS:
        doc = _parse(path)
        assert doc.error is None, f"{path} failed to parse: {doc.error}"
        assert doc.n_pages > 0
        assert len(doc.pages) == doc.n_pages


def test_every_page_has_text_or_is_flagged_image_only():
    for path in PDF_PATHS:
        doc = _parse(path)
        for page in doc.pages:
            assert page.text.strip() or page.image_only, (
                f"{path} page {page.page_no} has no text and is not flagged image_only"
            )


def test_bbox_round_trip_per_document():
    for path in PDF_PATHS:
        doc = _parse(path)
        page = next((p for p in doc.pages if p.words), None)
        assert page is not None, f"{path} has no page with extractable words"

        w = page.words[0]
        bbox = page.bbox_for_span(w.char_start, w.char_end)
        assert bbox == w.bbox

        w2 = page.words[min(3, len(page.words) - 1)]
        multi_bbox = page.bbox_for_span(w.char_start, w2.char_end)
        assert multi_bbox is not None
        assert multi_bbox[0] <= w.bbox[0]
        assert multi_bbox[2] >= w2.bbox[2]

        # the recovered span's text must equal what the word offsets claim
        snippet = page.text[w.char_start:w.char_end]
        assert snippet == w.text


def test_every_word_offset_resolves_to_its_own_text_on_every_page():
    """Stronger form of the bbox round-trip spot-check, across EVERY word of
    a sample page per document: page.text[w.char_start:w.char_end] must be
    exactly w.text. This is the invariant any future reading-order change
    would break first, and evidence mapping depends on it entirely."""
    for path in PDF_PATHS:
        doc = _parse(path)
        page = max(doc.pages, key=lambda p: len(p.words))
        assert page.words, f"{path} has no page with words"
        for w in page.words:
            assert page.text[w.char_start:w.char_end] == w.text, (
                f"{path} page {page.page_no}: offset {w.char_start} does not resolve to {w.text!r}"
            )


def test_reading_order_is_top_to_bottom_on_real_pages():
    """Coarse-grained check: bucket a page's words into deciles by char
    order and assert each decile's average `top` is non-decreasing. This is
    deliberately loose about individual-line jitter (real PDFs commonly have
    two lines whose word `top`s differ by only a point or two — well within
    normal baseline variation, not a reading-order bug) while still catching
    a genuine gross regression, e.g. a future layout change that interleaves
    a later column's top-of-page text with an earlier column's bottom-of-
    page text."""
    n_buckets = 10
    for path in PDF_PATHS:
        doc = _parse(path)
        page = max(doc.pages, key=lambda p: len(p.words))
        ordered = sorted(page.words, key=lambda w: w.char_start)
        if len(ordered) < n_buckets * 3:
            continue   # too few words on this page for decile bucketing to mean anything
        bucket_size = len(ordered) // n_buckets
        bucket_avgs = []
        for i in range(n_buckets):
            chunk = ordered[i * bucket_size: (i + 1) * bucket_size]
            bucket_avgs.append(sum(w.bbox[1] for w in chunk) / len(chunk))
        for i in range(1, len(bucket_avgs)):
            assert bucket_avgs[i] >= bucket_avgs[i - 1] - 20.0, (
                f"{path} page {page.page_no}: text bucket {i} average top "
                f"({bucket_avgs[i]:.1f}) regressed well above bucket {i - 1}'s "
                f"({bucket_avgs[i - 1]:.1f}) — reading order is not top-to-bottom"
            )


def test_imf_cover_page_is_image_only():
    imf_path = next(p for p in PDF_PATHS if "imf" in p.lower())
    doc = _parse(imf_path)
    assert doc.pages[0].image_only


def test_delhivery_table_has_scale_context():
    delhivery_paths = [p for p in PDF_PATHS if "delhivery" in p.lower()]
    found = []
    for path in delhivery_paths:
        doc = _parse(path)
        for page in doc.pages:
            for table in page.tables:
                if table.scale_context:
                    found.append((path, page.page_no, table.scale_context))
    assert found, "expected at least one Delhivery table with a detected scale_context"


def test_table_text_block_includes_scale_and_rows():
    delhivery_paths = [p for p in PDF_PATHS if "delhivery" in p.lower()]
    for path in delhivery_paths:
        doc = _parse(path)
        for page in doc.pages:
            for table in page.tables:
                if table.scale_context:
                    block = table.to_text_block()
                    assert table.scale_context in block
                    assert "|" in block or all(len(row) <= 1 for row in table.rows)
                    return
    raise AssertionError("no scale-context table found to verify to_text_block()")
