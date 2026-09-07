"""
Deterministic layout reconstruction (A5).

words -> lines -> blocks -> (confident columns, else unchanged order)

Purely additive: computed entirely from already-parsed Page/Word/Table data
(no PDF re-read) and NEVER touches Page.text / Word.char_start / char_end —
those remain the sole, unchanged basis for evidence (models.py's binding
contract, restated in every module that touches parsing). This module
answers "what visual structure does this page have?"; it is never consulted
to decide what a span of text IS for evidence purposes.

Column detection is deliberately conservative. A3's own investigation of
this corpus found that a naive "wide horizontal gap = two columns" rule
fires on 25.9% of long lines (4,902/18,902) — almost all of it table-
adjacent prose, not real multi-column layout. Multi-column detection here
requires several independent signals to agree (see _detect_confident_columns)
and defaults to "no column split" whenever they don't — the single-column,
already-correct reading order is the fallback, not a guess.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

from pdfplumber.utils import cluster_objects

from .parse import Page, Table, Word, _LINE_TOLERANCE

_BLOCK_GAP_HEIGHT_MULTIPLIER = 2.5   # a gap this many typical-line-heights wide starts a new block
_HEADING_MAX_WORDS = 10
_MIN_COLUMN_CANDIDATE_LINES = 8  # too few multi-word lines to say anything about columns
_WIDE_GAP_POINTS = 40.0          # candidate gutter gap, in PDF points
_GUTTER_POSITION_TOLERANCE = 15.0
_LIST_MARKER_RE = re.compile(r"^[-•*]\s|^\d+[.)]\s")
_DOT_LEADER_RE = re.compile(r"\.{3,}")   # table-of-contents style "Title .... 42" — a right-
                                          # aligned page number after a dot leader creates a
                                          # spatially stable gap that is not a column gutter


@dataclass
class Line:
    words: list[Word]
    top: float
    bottom: float
    x0: float
    x1: float

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Block:
    lines: list[Line]
    bbox: tuple[float, float, float, float]
    block_type: str = "unknown"   # "paragraph" | "heading" | "table_adjacent" | "list" | "unknown"


@dataclass
class PageLayout:
    page_no: int
    lines: list[Line] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    multi_column_detected: bool = False
    column_boundary_x: Optional[float] = None


def _group_words_into_lines(words: list[Word]) -> list[Line]:
    """Same spatial-proximity clustering parse.py's own text assembly uses
    (cluster by `top` within _LINE_TOLERANCE) — reused, not reimplemented,
    just run again here over the already-built Word list rather than raw
    pdfplumber dicts, since Page doesn't persist the intermediate clusters."""
    if not words:
        return []
    raw = [{"top": w.bbox[1], "_w": w} for w in words]
    clusters = cluster_objects(raw, "top", _LINE_TOLERANCE)
    lines: list[Line] = []
    for cluster in clusters:
        ws = sorted((c["_w"] for c in cluster), key=lambda w: w.bbox[0])
        lines.append(Line(
            words=ws,
            top=min(w.bbox[1] for w in ws),
            bottom=max(w.bbox[3] for w in ws),
            x0=min(w.bbox[0] for w in ws),
            x1=max(w.bbox[2] for w in ws),
        ))
    lines.sort(key=lambda l: l.top)
    return lines


def _overlaps_any_table(bbox: tuple[float, float, float, float], tables: list[Table]) -> bool:
    x0, top, x1, bottom = bbox
    for t in tables:
        tx0, ttop, tx1, tbottom = t.bbox
        if not (bottom < ttop or top > tbottom or x1 < tx0 or x0 > tx1):
            return True
    return False


def _classify_block(lines: list[Line], tables: list[Table]) -> str:
    x0 = min(l.x0 for l in lines); x1 = max(l.x1 for l in lines)
    top = min(l.top for l in lines); bottom = max(l.bottom for l in lines)
    if _overlaps_any_table((x0, top, x1, bottom), tables):
        return "table_adjacent"
    if len(lines) == 1:
        line = lines[0]
        if len(line.words) <= _HEADING_MAX_WORDS and not _LIST_MARKER_RE.match(line.text):
            return "heading"
        return "unknown"
    if any(_LIST_MARKER_RE.match(l.text) for l in lines):
        return "list"
    return "paragraph"


def _group_lines_into_blocks(lines: list[Line], tables: list[Table]) -> list[Block]:
    """Blocks split at a vertical gap noticeably wider than this page's own
    typical LINE HEIGHT — a per-page adaptive threshold, not a fixed
    constant, since line spacing varies by document (tight tables vs. loose
    prose). Deliberately anchored to line height rather than the gaps
    between lines: with very few lines (as few as two), the "typical gap"
    computed from the gaps themselves is self-referential — the one gap
    IS the sample, so no gap can ever exceed a multiple of itself. Line
    height is stable and available even from a single line. Classification
    is a small, explicitly-fallible label set; "unknown" is a legitimate,
    expected outcome (A5.3), not a bug."""
    if not lines:
        return []
    heights = [max(l.bottom - l.top, 1.0) for l in lines]
    typical_height = statistics.median(heights)
    threshold = max(typical_height * _BLOCK_GAP_HEIGHT_MULTIPLIER, _LINE_TOLERANCE * 2)

    groups: list[list[Line]] = [[lines[0]]]
    for i in range(1, len(lines)):
        gap = lines[i].top - lines[i - 1].bottom
        if gap > threshold:
            groups.append([lines[i]])
        else:
            groups[-1].append(lines[i])

    blocks = []
    for group in groups:
        bbox = (min(l.x0 for l in group), min(l.top for l in group),
                max(l.x1 for l in group), max(l.bottom for l in group))
        blocks.append(Block(lines=group, bbox=bbox, block_type=_classify_block(group, tables)))
    return blocks


def _detect_confident_columns(lines: list[Line], tables: list[Table]) -> tuple[bool, Optional[float]]:
    """Multi-signal, conservative by design (see module docstring). A
    genuine column gutter must be: (1) wide, (2) present on a clear majority
    of multi-word lines, AND (3) spatially STABLE — recurring at close to
    the same x-position across those lines. A single wide gap on an
    isolated line (the table-adjacent false-positive this corpus actually
    exhibits) satisfies none of the last two conditions reliably; requiring
    all three together is what keeps this from firing on this corpus's own
    demonstrated 26% wide-gap rate. Lines overlapping a table are excluded
    entirely — that content already has its own structural representation."""
    candidates = [l for l in lines if len(l.words) >= 4
                  and not _overlaps_any_table((l.x0, l.top, l.x1, l.bottom), tables)
                  and not _DOT_LEADER_RE.search(l.text)]
    if len(candidates) < _MIN_COLUMN_CANDIDATE_LINES:
        return False, None

    gutter_positions = []
    for line in candidates:
        ws = line.words
        best_gap, best_x = 0.0, None
        for i in range(len(ws) - 1):
            gap = ws[i + 1].bbox[0] - ws[i].bbox[2]
            if gap > best_gap:
                best_gap, best_x = gap, (ws[i].bbox[2] + ws[i + 1].bbox[0]) / 2
        if best_gap > _WIDE_GAP_POINTS:
            gutter_positions.append(best_x)

    if len(gutter_positions) < 0.5 * len(candidates):
        return False, None

    median_x = statistics.median(gutter_positions)
    stable = [x for x in gutter_positions if abs(x - median_x) < _GUTTER_POSITION_TOLERANCE]
    if len(stable) >= 0.7 * len(gutter_positions) and len(stable) >= 0.5 * len(candidates):
        return True, median_x
    return False, None


def build_page_layout(page: Page) -> PageLayout:
    """O(words) — one clustering pass plus a handful of linear scans. No PDF
    re-read; operates entirely on the already-parsed Page."""
    lines = _group_words_into_lines(page.words)
    blocks = _group_lines_into_blocks(lines, page.tables)
    multi_col, boundary = _detect_confident_columns(lines, page.tables)
    return PageLayout(page_no=page.page_no, lines=lines, blocks=blocks,
                       multi_column_detected=multi_col, column_boundary_x=boundary)
