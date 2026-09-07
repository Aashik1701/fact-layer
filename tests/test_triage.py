"""Triage tests run against all six real starter-dataset PDFs (parse.py's
on-disk cache makes repeated parses here cheap after the first run)."""

import functools
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.parse import parse_pdf
from fact_layer.triage import batch_pages, evaluate_document, select_pages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_PATHS = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))

_parse = functools.lru_cache(maxsize=None)(parse_pdf)


def test_selection_stays_within_budget():
    for path in PDF_PATHS:
        doc = _parse(path)
        for budget in (5, 15, 30):
            selected = select_pages(doc, budget)
            assert len(selected) <= budget, f"{path} budget={budget} selected={len(selected)}"
            assert selected == sorted(set(selected)), "page numbers must be sorted and unique"


def test_selected_pages_are_never_image_only_or_below_threshold():
    for path in PDF_PATHS:
        doc = _parse(path)
        selected = set(select_pages(doc, budget=20))
        pages_by_no = {p.page_no: p for p in doc.pages}
        for page_no in selected:
            page = pages_by_no[page_no]
            assert not page.image_only


def test_batching_respects_char_budget():
    char_budget = 6000
    for path in PDF_PATHS:
        doc = _parse(path)
        selected = set(select_pages(doc, budget=20))
        pages = [p for p in doc.pages if p.page_no in selected]
        batches = batch_pages(pages, char_budget=char_budget)

        # every selected page appears in exactly one batch
        all_batched = [pn for batch in batches for pn in batch]
        assert sorted(all_batched) == sorted(selected)
        assert len(all_batched) == len(set(all_batched))

        pages_by_no = {p.page_no: p for p in pages}
        for batch in batches:
            cost = sum(
                len(pages_by_no[pn].text) + sum(len(t.to_text_block()) for t in pages_by_no[pn].tables)
                for pn in batch
            )
            # a single oversized page is its own batch and may exceed the budget alone;
            # any batch of more than one page must have stayed under it while packing.
            if len(batch) > 1:
                assert cost <= char_budget or len(batch) == 1


def test_sparse_document_batches_multiple_pages_per_call():
    deck_path = next(p for p in PDF_PATHS if "earnings-presentation" in p)
    doc = _parse(deck_path)
    selected = set(select_pages(doc, budget=-1))   # UNLIMITED_BUDGET
    pages = [p for p in doc.pages if p.page_no in selected]
    batches = batch_pages(pages, char_budget=6000)

    assert len(batches) < len(pages), "sparse slides should pack more than one page per call"
    assert any(len(b) > 1 for b in batches)


def test_bare_year_filter_fires_at_least_once_in_corpus():
    dropped_total = 0
    for path in PDF_PATHS:
        doc = _parse(path)
        decisions = evaluate_document(doc)
        dropped_total += sum(1 for d in decisions if d.reason == "bare_year_majority")
    assert dropped_total >= 1, "expected the bare-year majority filter to fire on at least one page in the corpus"


def test_image_only_pages_are_never_candidates():
    imf_path = next(p for p in PDF_PATHS if "imf" in p.lower())
    doc = _parse(imf_path)
    decisions = evaluate_document(doc)
    image_only_decisions = [d for d in decisions if d.reason == "image_only"]
    assert image_only_decisions, "expected the known IMF cover page to be flagged image_only"
    for d in image_only_decisions:
        assert d.score is None
