"""
Document/page parsing diagnostics.

The parser (parse.py) never pretends every page is equally trustworthy: a
scanned cover page and a dense financial-statements page produce very
different Page objects already (image_only, word count, table count). This
module answers one question from that existing data — "can I trust this
document's extraction pipeline?" — without inventing a fake confidence
score. Everything here is a deterministic count or an explicit warning
string; nothing here rejects a fact or changes evidence. A parser warning
and a fact rejection are different concerns (extract.py/value_verify.py own
the latter).

Reuses Page/Document exactly as parse.py already produces them — no new
PDF reads, no re-parsing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .parse import Document, Page

# A page with SOME text but fewer words than this is flagged "sparse" for
# visibility — distinct from `image_only` (parse.py's existing, stricter
# "zero extractable text" signal, left unchanged). A sparse page still goes
# through extraction normally; this is a warning, not a gate.
_SPARSE_WORD_THRESHOLD = 10

# A normalized top/bottom line must recur on at least this many pages, or
# this fraction of text-bearing pages (whichever is larger), to be called a
# repeated header/footer candidate. Conservative on purpose: a phrase that
# happens to open two unrelated pages is not "structural noise" — it takes
# recurring across a meaningful share of the document to earn that label.
_REPEAT_MIN_PAGES = 3
_REPEAT_MIN_FRACTION = 0.3
_EDGE_LINES_CHECKED = 2   # top-2 / bottom-2 non-blank lines per page

_DIGIT_RUN_RE = re.compile(r"\d+")


@dataclass
class PageDiagnostics:
    page_number: int
    text_length: int
    word_count: int
    table_count: int
    image_only: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentDiagnostics:
    total_pages: int
    text_pages: int
    image_only_pages: int
    sparse_pages: int
    tables_detected: int
    pages_with_tables: int
    repeated_header_candidates: list[str] = field(default_factory=list)
    repeated_footer_candidates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: list[PageDiagnostics] = field(default_factory=list)


def _normalize_edge_line(text: str) -> str:
    """Collapse a candidate header/footer line to a comparable key: case-
    and whitespace-insensitive, and digit runs collapsed to '#' so a page
    number ("Page 47" / "Page 48") or a running year still matches across
    pages instead of registering as a different line every time."""
    t = " ".join(text.split()).lower()
    return _DIGIT_RUN_RE.sub("#", t)


def _edge_lines(page: Page) -> tuple[list[str], list[str]]:
    """Top-N / bottom-N non-blank lines of the page's already-assembled,
    reading-order text — no re-reading the PDF, no new coordinate work."""
    lines = [ln.strip() for ln in page.text.split("\n") if ln.strip()]
    top = lines[:_EDGE_LINES_CHECKED]
    bottom = lines[-_EDGE_LINES_CHECKED:] if len(lines) > _EDGE_LINES_CHECKED else []
    return top, bottom


def detect_repeated_edge_lines(doc: Document) -> tuple[list[str], list[str]]:
    """Repeated-header and repeated-footer candidates, by literal recurrence
    of a normalized top/bottom line across a meaningful share of pages. This
    is detection for extraction CONTEXT only: parse.py's page text and
    offsets are never modified, and no evidence is altered — a repeated
    header still verifies exactly as before if an LLM ever quotes it."""
    text_pages = [p for p in doc.pages if not p.image_only and p.text.strip()]
    if not text_pages:
        return [], []

    threshold = max(_REPEAT_MIN_PAGES, math.ceil(_REPEAT_MIN_FRACTION * len(text_pages)))

    header_counts: dict[str, int] = {}
    header_examples: dict[str, str] = {}
    footer_counts: dict[str, int] = {}
    footer_examples: dict[str, str] = {}

    for page in text_pages:
        top, bottom = _edge_lines(page)
        for line in top:
            key = _normalize_edge_line(line)
            if not key:
                continue
            header_counts[key] = header_counts.get(key, 0) + 1
            header_examples.setdefault(key, line)
        for line in bottom:
            key = _normalize_edge_line(line)
            if not key:
                continue
            footer_counts[key] = footer_counts.get(key, 0) + 1
            footer_examples.setdefault(key, line)

    headers = [
        f"{header_examples[k]!r} (near the top of {c} of {len(text_pages)} pages)"
        for k, c in header_counts.items() if c >= threshold
    ]
    footers = [
        f"{footer_examples[k]!r} (near the bottom of {c} of {len(text_pages)} pages)"
        for k, c in footer_counts.items() if c >= threshold
    ]
    return headers, footers


def compute_page_diagnostics(page: Page) -> PageDiagnostics:
    word_count = len(page.words)
    warnings: list[str] = []
    if page.image_only:
        warnings.append("no extractable text (image-only or scanned page)")
    elif word_count < _SPARSE_WORD_THRESHOLD:
        warnings.append(f"sparse text ({word_count} word(s)) — extraction may miss context")
    if page.error:
        warnings.append(f"parse warning: {page.error}")
    return PageDiagnostics(
        page_number=page.page_no,
        text_length=len(page.text),
        word_count=word_count,
        table_count=len(page.tables),
        image_only=page.image_only,
        warnings=warnings,
    )


def compute_document_diagnostics(doc: Document) -> DocumentDiagnostics:
    """Deterministic, O(pages) — one pass over already-parsed Page objects
    plus the single repeated-edge-line pass above. No new PDF reads."""
    pages = [compute_page_diagnostics(p) for p in doc.pages]
    image_only_pages = sum(1 for p in pages if p.image_only)
    sparse_pages = sum(1 for p in pages if not p.image_only and p.word_count < _SPARSE_WORD_THRESHOLD)
    text_pages = len(pages) - image_only_pages
    tables_detected = sum(p.table_count for p in pages)
    pages_with_tables = sum(1 for p in pages if p.table_count > 0)

    headers, footers = detect_repeated_edge_lines(doc)

    warnings: list[str] = []
    if image_only_pages:
        warnings.append(f"{image_only_pages} of {len(pages)} page(s) contain little/no extractable text")
    if sparse_pages:
        warnings.append(f"{sparse_pages} page(s) have sparse text (may lack full context)")
    if headers:
        warnings.append(f"repeated header detected across the document ({len(headers)} candidate line(s))")
    if footers:
        warnings.append(f"repeated footer detected across the document ({len(footers)} candidate line(s))")

    return DocumentDiagnostics(
        total_pages=len(pages),
        text_pages=text_pages,
        image_only_pages=image_only_pages,
        sparse_pages=sparse_pages,
        tables_detected=tables_detected,
        pages_with_tables=pages_with_tables,
        repeated_header_candidates=headers,
        repeated_footer_candidates=footers,
        warnings=warnings,
        pages=pages,
    )
