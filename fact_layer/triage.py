"""
Deterministic, zero-LLM page triage.

Six PDFs run to 511 pages; one LLM call per page would blow the free-tier
budget five times over (project specification section 10). Page selection has to be an
architectural component, not an afterthought — and it has to be decided
without an LLM, using the same locale-general number/period detectors
normalize.py already has, not a new hand-rolled vocabulary of measure names.

A page is a candidate only if it has both a period anchor and enough numeric
density to be worth a call; low-density pages (front matter, prose, TOCs) are
free to skip.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .normalize import (
    _ASON_RE,
    _DATE_PATTERNS,
    _ENDED_RE,
    _FY_RE,
    _FY_SINGLE_RE,
    _NUM_RE,
    _QTR_RE,
    parse_period,
    parse_quantity,
)
from .parse import Document, Page, parse_pdf

# Every period-phrase pattern normalize.parse_period() itself understands.
# Triage does not invent new phrasing rules — it reuses these to find
# candidate spans, then hands each one to parse_period() to confirm it.
_PERIOD_PATTERNS = [_QTR_RE, _FY_RE, _FY_SINGLE_RE, _ENDED_RE, _ASON_RE, *_DATE_PATTERNS]

_BARE_YEAR_MIN = 1990
_BARE_YEAR_MAX = 2035
_BARE_YEAR_RE = re.compile(r"^\d{4}$")
_BARE_YEAR_RATIO_THRESHOLD = 0.6

_CANDIDATE_MIN_PERIODS = 1
_CANDIDATE_MIN_QUANTS = 3


def _period_key(p) -> tuple:
    return (p.kind.value, p.start.isoformat() if p.start else None, p.end.isoformat() if p.end else None)


def _count_distinct_periods(text: str) -> int:
    """Distinct periods parse_period() recognises in `text`, deduped by the
    window they resolve to (so 'FY2023-24' appearing five times counts once)."""
    if not text:
        return 0
    seen = set()
    for pattern in _PERIOD_PATTERNS:
        for m in pattern.finditer(text):
            period = parse_period(m.group(0))
            if period is not None:
                seen.add(_period_key(period))
    return len(seen)


def _is_bare_year(q, raw_text: str) -> bool:
    """A 4-digit literal that parse_quantity() happily parses as a Quantity
    but that carries no numeric information — 'FY2024', a page footer, a
    table column year header. Distinguished from a genuine 4-digit measurement
    by checking the parse picked up no currency/scale/percent marker at all."""
    stripped = raw_text.strip()
    if not _BARE_YEAR_RE.match(stripped):
        return False
    if q.unit != "count" or q.currency is not None:
        return False
    if q.value != Decimal(stripped):
        return False       # a scale/percent marker changed the value -> not bare
    year = int(stripped)
    return _BARE_YEAR_MIN <= year <= _BARE_YEAR_MAX


def _count_quantities(text: str, context: str = "") -> tuple[int, int]:
    """(total parse_quantity() hits, of which bare-year hits) in `text`.
    `context` (typically a table's scale_context) is combined with a small
    local window after each literal so nearby currency/scale/percent markers
    are still picked up for plain prose numbers, not just table cells."""
    total = 0
    bare_years = 0
    if not text:
        return total, bare_years
    for m in _NUM_RE.finditer(text):
        literal = m.group(0)
        window = text[m.end():m.end() + 15]
        q = parse_quantity(literal, f"{context} {window}".strip())
        if q is None:
            continue
        total += 1
        if _is_bare_year(q, literal):
            bare_years += 1
    return total, bare_years


@dataclass
class PageScore:
    page_no: int
    periods: int
    quants: int
    bare_year_count: int
    bare_year_ratio: float
    has_scale_context: bool
    is_candidate: bool
    score: float


def score_page(page: Page, table_scale_contexts: Optional[list[str]] = None) -> PageScore:
    """Score one page's worth of extracting-worthiness with zero LLM calls.

    `table_scale_contexts`, if given, must be parallel to `page.tables` (one
    scale-context string per table); when omitted, each table's own
    `.scale_context` — already detected by parse.py's header sniffer — is
    used directly.
    """
    periods = _count_distinct_periods(page.text)
    total_quants, bare_years = _count_quantities(page.text)

    contexts = table_scale_contexts if table_scale_contexts is not None else [t.scale_context for t in page.tables]
    has_scale_context = any(contexts)

    for table, ctx in zip(page.tables, contexts):
        for row in table.rows:
            for cell in row:
                if not cell:
                    continue
                t_total, t_bare = _count_quantities(cell, ctx)
                total_quants += t_total
                bare_years += t_bare

    bare_year_ratio = (bare_years / total_quants) if total_quants else 0.0
    is_candidate = periods >= _CANDIDATE_MIN_PERIODS and total_quants >= _CANDIDATE_MIN_QUANTS
    score = min(periods, 5) * math.log(1 + total_quants) * (1.5 if has_scale_context else 1.0)

    return PageScore(
        page_no=page.page_no, periods=periods, quants=total_quants,
        bare_year_count=bare_years, bare_year_ratio=bare_year_ratio,
        has_scale_context=has_scale_context, is_candidate=is_candidate, score=score,
    )


@dataclass
class PageDecision:
    page_no: int
    score: Optional[PageScore]
    reason: str   # "candidate" | "not_candidate" | "bare_year_majority" | "image_only"


def evaluate_document(document: Document) -> list[PageDecision]:
    """Score every page and classify it, without yet applying a budget."""
    decisions: list[PageDecision] = []
    for page in document.pages:
        if page.image_only:
            decisions.append(PageDecision(page.page_no, None, "image_only"))
            continue

        s = score_page(page)
        if s.bare_year_ratio > _BARE_YEAR_RATIO_THRESHOLD:
            decisions.append(PageDecision(page.page_no, s, "bare_year_majority"))
        elif not s.is_candidate:
            decisions.append(PageDecision(page.page_no, s, "not_candidate"))
        else:
            decisions.append(PageDecision(page.page_no, s, "candidate"))
    return decisions


def select_pages(document: Document, budget: int) -> list[int]:
    """Candidate pages sorted by score desc, truncated to `budget`.

    A negative budget (see UNLIMITED_BUDGET below) or None means no cap —
    every candidate is kept. This must NOT be implemented as `candidates[:budget]`
    with budget=-1: Python slicing treats a negative stop as "all but the
    last N", which silently drops the lowest-scoring candidate instead of
    keeping all of them.
    """
    decisions = evaluate_document(document)
    candidates = [d for d in decisions if d.reason == "candidate"]
    candidates.sort(key=lambda d: d.score.score, reverse=True)
    chosen = candidates if (budget is None or budget < 0) else candidates[:budget]
    return sorted(d.page_no for d in chosen)


def batch_pages(pages: list[Page], char_budget: int = 6000) -> list[list[int]]:
    """Pack consecutive selected pages (in document order) into groups whose
    combined text+table char cost stays under `char_budget`, so a sparse
    27-page deck costs one call per handful of slides, not one per slide."""

    def page_cost(p: Page) -> int:
        return len(p.text) + sum(len(t.to_text_block()) for t in p.tables)

    ordered = sorted(pages, key=lambda p: p.page_no)
    batches: list[list[int]] = []
    current: list[int] = []
    current_cost = 0
    for p in ordered:
        cost = page_cost(p)
        if current and current_cost + cost > char_budget:
            batches.append(current)
            current = []
            current_cost = 0
        current.append(p.page_no)
        current_cost += cost
    if current:
        batches.append(current)
    return batches


# --------------------------------------------------------------------------
# Report generation / CLI
# --------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_REPORT_PATH = os.path.join(_REPO_ROOT, "data", "triage_report.json")

# Distinct from the `budget=None` parameter default (which means "not
# specified by the caller -> use the generic page-count formula"). This means
# "the caller explicitly wants every candidate, no cap" — e.g. the sparse
# earnings deck, where the natural candidacy filter already does the work.
UNLIMITED_BUDGET = -1

# Budgets an operator would pass in for this specific 6-PDF demo corpus,
# matching the project specification's measured target (~101 pages -> ~59 calls).
# This dict is report-generation config for the CLI's default run over
# starter-datasets/ — score_page/select_pages/batch_pages never see a
# filename, and any other PDF just gets the generic page-count-derived
# default below.
_DEMO_BUDGETS = {
    "01-delhivery-prospectus-2022-excerpt.pdf": 15,
    "02-delhivery-annual-report-fy24-excerpt.pdf": 20,
    "03-delhivery-q4-fy24-earnings-presentation.pdf": UNLIMITED_BUDGET,   # "all", still candidacy-gated
    "01-india-economic-survey-2024-25-excerpt.pdf": 12,
    "02-rbi-annual-report-2024-25-excerpt.pdf": 15,
    "03-imf-india-2025-article-iv-excerpt.pdf": 12,
}


def default_budget(n_pages: int) -> int:
    return min(20, max(8, n_pages // 6))


def build_document_report(path: str, budget: Optional[int] = None, char_budget: int = 6000) -> dict:
    document = parse_pdf(path)
    if budget is None:
        budget = default_budget(document.n_pages)

    # select_pages() is the single source of truth for "which candidates make
    # the cut, including the UNLIMITED_BUDGET sentinel" — do not re-derive
    # that logic here, that duplication is exactly how it drifted out of sync
    # before.
    chosen_nos = set(select_pages(document, budget))
    decisions = evaluate_document(document)

    for d in decisions:
        if d.reason == "candidate":
            d.reason = "selected" if d.page_no in chosen_nos else "over_budget"

    selected_pages = [p for p in document.pages if p.page_no in chosen_nos]
    batches = batch_pages(selected_pages, char_budget)
    bare_year_dropped = sum(1 for d in decisions if d.reason == "bare_year_majority")

    return {
        "doc_id": document.doc_id,
        "filename": document.filename,
        "n_pages": document.n_pages,
        "table_strategy": document.table_strategy,
        "budget": budget,
        "char_budget": char_budget,
        "pages_selected": [
            {"page_no": d.page_no, "score": round(d.score.score, 3), "periods": d.score.periods,
             "quants": d.score.quants, "has_scale_context": d.score.has_scale_context}
            for d in decisions if d.reason == "selected"
        ],
        "pages_rejected": [
            {
                "page_no": d.page_no,
                "reason": d.reason,
                **({"score": round(d.score.score, 3), "bare_year_ratio": round(d.score.bare_year_ratio, 2)}
                   if d.score is not None else {}),
            }
            for d in decisions if d.reason != "selected"
        ],
        "bare_year_dropped_pages": bare_year_dropped,
        "batches": batches,
        "projected_calls": len(batches),
    }


def build_full_report(paths: list[str], budgets: dict[str, Optional[int]], char_budget: int = 6000) -> dict:
    documents = []
    total_calls = 0
    for path in paths:
        budget = budgets.get(os.path.basename(path))
        doc_report = build_document_report(path, budget, char_budget)
        documents.append(doc_report)
        total_calls += doc_report["projected_calls"]
    return {"documents": documents, "projected_total_calls": total_calls}


def _print_summary(report: dict) -> None:
    print(f"{'document':55s} {'pages':>6s} {'selected':>9s} {'batches':>8s} {'calls':>6s}")
    print("-" * 90)
    for d in report["documents"]:
        print(f"{d['filename']:55s} {d['n_pages']:6d} {len(d['pages_selected']):9d} "
              f"{len(d['batches']):8d} {d['projected_calls']:6d}")
    print("-" * 90)
    print(f"projected total calls across all documents: {report['projected_total_calls']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic, LLM-free page triage.")
    parser.add_argument("pdfs", nargs="*", help="PDF paths; defaults to all starter-dataset PDFs")
    parser.add_argument("--budget", type=int, default=None,
                        help="Per-document page budget applied to every PDF given. Omit to use "
                             "the demo-corpus defaults (starter-datasets/) or the generic "
                             "min(20, max(8, n_pages // 6)) formula for any other PDF.")
    parser.add_argument("--char-budget", type=int, default=6000)
    parser.add_argument("--out", default=_DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    if args.pdfs:
        paths = args.pdfs
        budgets = {os.path.basename(p): args.budget for p in paths}
    else:
        paths = sorted(glob.glob(os.path.join(_REPO_ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
        budgets = {k: (args.budget if args.budget is not None else v) for k, v in _DEMO_BUDGETS.items()}

    report = build_full_report(paths, budgets, args.char_budget)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    _print_summary(report)


if __name__ == "__main__":
    main()
