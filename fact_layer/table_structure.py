"""
First-class semantic table structure (A6).

Builds a structured, cell-level view ON TOP OF the existing Table
(parse.py) — never replaces it, never touches evidence, never touches
Table.to_text_block() (the LLM-facing string hashed into the replay-cache
key). This module answers "what row/column/unit/period does this cell
belong to?" as an internal, additive representation; it is consulted by
value_verify.py (A7) and extract.py (A8) for CONTEXT, never as a second
source of evidence.

Ambiguity is a first-class, expected outcome (A6.6), not a failure mode:
if the table's own structure doesn't establish a cell's row label, column
header, or which of several numbers within a cell is "the" value, this
module says so — it never guesses a structure the table doesn't actually
show. This mirrors value_verify.py's own multi-candidate-number principle
(never bless one of several possible numbers just because it matches).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

from .normalize import parse_period
from .parse import Table
from .value_verify import _candidate_quantities

_MAX_HEADER_LABEL_LEN = 15


@dataclass(frozen=True)
class CellContext:
    text: str
    row_index: int                  # index into the table's DATA rows (header row(s) excluded)
    column_index: int
    bbox: Optional[tuple[float, float, float, float]]
    row_label: Optional[str]
    column_header: Optional[str]
    unit: str
    period_label: Optional[str]     # set only when column_header confidently parses as a period
    ambiguous: bool
    source_page: int


@dataclass
class StructuredTable:
    table: Table
    caption: str
    unit: str
    header_row_count: int                    # 0, 1, or 2 (multi-level)
    column_headers: list[str]
    row_labels: list[Optional[str]]
    row_label_confident: bool                # False when column 0 looks numeric, not a label
    cells: list[list[CellContext]]           # [data_row][column], header rows excluded


_WHOLE_CELL_NUMERIC_RE = re.compile(r"^[-+]?[₹$€£]?\s*\d[\d,]*\.?\d*\s*%?$")


def _is_numeric_cell(text: str) -> bool:
    """True only when the ENTIRE cell is (essentially) a number — not
    merely contains a digit somewhere (a label like "Segment 12" or
    "Note 3" must never be misread as a numeric data cell just because it
    has a trailing digit)."""
    return bool(_WHOLE_CELL_NUMERIC_RE.match((text or "").strip()))


def _detect_header_row_count(table: Table) -> int:
    """1 by default (the existing convention extract.py already relies on);
    2 only for the specific, narrow shape of a genuine merged two-level
    header — a top row with AT LEAST TWO DISTINCT, short group labels (e.g.
    "2024" and "2025", each spanning some blank continuation cells) and a
    fully-populated second row of short, non-numeric sub-labels ("Actual"/
    "Forecast"). Requiring >=2 distinct group labels — not just >=1
    non-empty cell — is what tells a genuine hierarchical header apart from
    a single caption/chart-title row sitting on top of an ordinary header:
    real examples found in this corpus ("Chart IV.14: Household consumption
    is lower than the production", "As of end of / for the period") each
    have exactly ONE non-empty row-0 cell and are captions, not groupings;
    every one is correctly excluded by this stricter rule."""
    rows = table.rows
    if not rows:
        return 0
    if len(rows) < 3:
        return 1
    row0, row1 = rows[0], rows[1]
    if len(row0) != len(row1) or len(row0) < 2:
        return 1
    distinct_group_labels = {(c or "").strip() for c in row0 if (c or "").strip()}
    if len(distinct_group_labels) < 2:
        return 1
    if any(len(v) > _MAX_HEADER_LABEL_LEN for v in distinct_group_labels):
        return 1   # a genuine "2024"/"2025" group label is short; a caption is not
    tail1 = [(c or "").strip() for c in row1[1:]]
    row1_all_filled = all(tail1)
    row1_all_short_labels = all(len(c) <= _MAX_HEADER_LABEL_LEN and not _is_numeric_cell(c) for c in tail1)
    if row1_all_filled and row1_all_short_labels:
        return 2
    return 1


def _combined_column_headers(table: Table, header_row_count: int) -> list[str]:
    if header_row_count == 0 or not table.rows:
        return []
    n_cols = len(table.rows[0])
    headers = [""] * n_cols
    if header_row_count == 1:
        return [(c or "").strip() for c in table.rows[0]]

    # Two-level: forward-fill row 0's merged/spanning label left-to-right
    # (pdfplumber stores a spanning header's text once, at its first
    # column, and blank in the columns it spans over), then combine with
    # row 1's own sub-label.
    top_filled = ""
    top_per_col = []
    for c in range(n_cols):
        cell = (table.rows[0][c] or "").strip() if c < len(table.rows[0]) else ""
        if cell:
            top_filled = cell
        top_per_col.append(top_filled)
    for c in range(n_cols):
        sub = (table.rows[1][c] or "").strip() if c < len(table.rows[1]) else ""
        headers[c] = f"{top_per_col[c]} {sub}".strip()
    return headers


def structure_table(table: Table) -> StructuredTable:
    """Deterministic, O(rows * cols). Pure function of the already-parsed
    Table — no PDF access, no LLM call."""
    header_row_count = _detect_header_row_count(table)
    column_headers = _combined_column_headers(table, header_row_count)
    data_rows = table.rows[header_row_count:]
    data_bboxes = (table.cell_bboxes[header_row_count:]
                   if len(table.cell_bboxes) >= header_row_count else [])

    # A6.2: don't assume column 0 is always the row label — if most of
    # column 0's data cells look numeric, this table's first column is
    # itself a data column, not a label column, and treating it as one
    # would misattribute every other cell in the row.
    first_col_values = [(row[0] or "").strip() for row in data_rows if row]
    numeric_first_col = sum(1 for v in first_col_values if v and _is_numeric_cell(v))
    non_empty_first_col = sum(1 for v in first_col_values if v)
    row_label_confident = not (non_empty_first_col > 0 and numeric_first_col > non_empty_first_col / 2)

    row_labels: list[Optional[str]] = []
    cells: list[list[CellContext]] = []
    for r, row in enumerate(data_rows):
        label = (row[0] or "").strip() if row_label_confident and row else None
        row_labels.append(label if label else None)
        bbox_row = data_bboxes[r] if r < len(data_bboxes) else []
        row_cells: list[CellContext] = []
        for c, raw_text in enumerate(row):
            text = (raw_text or "").strip()
            header = column_headers[c] if c < len(column_headers) else None
            period = None
            if header:
                p = parse_period(header)
                if p is not None:
                    period = p.label or header
            # A cell is ambiguous if it itself packs more than one distinct
            # numeric candidate (a table cell that is really several
            # sub-values run together — the real ESOPs-row shape) OR if
            # the row label itself could not be confidently established.
            ambiguous = (not row_label_confident) or len(_candidate_quantities(text, table.scale_context)) > 1
            bbox = bbox_row[c] if c < len(bbox_row) else None
            row_cells.append(CellContext(
                text=text, row_index=r, column_index=c, bbox=bbox,
                row_label=label,
                column_header=header or None,
                unit=table.scale_context, period_label=period,
                ambiguous=ambiguous, source_page=table.page_no,
            ))
        cells.append(row_cells)

    return StructuredTable(
        table=table, caption=table.caption, unit=table.scale_context,
        header_row_count=header_row_count, column_headers=column_headers,
        row_labels=row_labels, row_label_confident=row_label_confident, cells=cells,
    )


def get_cell_context(structured: StructuredTable, row: int, col: int) -> Optional[CellContext]:
    """A6.5's requested lookup API: cell_context(table, row, col). Direct
    indexing into `structured.cells` already works; this wrapper exists so
    callers don't need to know the internal list-of-lists shape, and
    returns None (never raises) for an out-of-range request."""
    if 0 <= row < len(structured.cells) and 0 <= col < len(structured.cells[row]):
        return structured.cells[row][col]
    return None


def find_cells_matching_value(structured: StructuredTable, value_raw: str) -> list[CellContext]:
    """Every NON-ambiguous cell in the table whose own text supports a
    number agreeing with value_raw's parsed quantity, used by A7/A8 to
    attribute a fact's value to a specific cell. Deliberately returns ALL
    matches, never picks one: a caller seeing more than one match must
    treat the attribution as unresolved (A6.6) rather than choosing
    arbitrarily. Reuses adjudicate._values_agree() for the same sig-fig
    tolerance the rest of the pipeline uses — never a new comparison rule."""
    from .adjudicate import _values_agree
    from .normalize import parse_quantity

    target = parse_quantity(value_raw, structured.unit)
    if target is None:
        return []
    matches = []
    for row in structured.cells:
        for cell in row:
            if cell.ambiguous:
                continue
            candidates = _candidate_quantities(cell.text, structured.unit)
            if len(candidates) != 1:
                continue
            candidate = candidates[0]
            same_currency = candidate.currency == target.currency or not (candidate.currency and target.currency)
            if same_currency and _values_agree(target, candidate)[0]:
                matches.append(cell)
    return matches


def table_id(doc_id: str, table: Table) -> str:
    """Deterministic, stable identifier for one table within one document:
    doc_id + page + a short hash of the table's own bbox (a page can hold
    more than one table, so page number alone isn't unique). Recomputed the
    same way every time from data that doesn't change between ingests, so
    the same table always gets the same id — never persisted as a
    freestanding counter that could drift."""
    bbox_key = ",".join(f"{v:.1f}" for v in table.bbox)
    digest = hashlib.sha1(bbox_key.encode()).hexdigest()[:8]
    return f"{doc_id}_p{table.page_no}_{digest}"


def _bbox_within(inner: tuple[float, float, float, float],
                  outer: tuple[float, float, float, float], tolerance: float = 2.0) -> bool:
    ix0, itop, ix1, ibottom = inner
    ox0, otop, ox1, obottom = outer
    return (ix0 >= ox0 - tolerance and itop >= otop - tolerance
            and ix1 <= ox1 + tolerance and ibottom <= obottom + tolerance)


def locate_cell(
    tables: list[Table], page_no: int, bbox: Optional[tuple[float, float, float, float]], value_raw: str,
) -> Optional[tuple[Table, CellContext]]:
    """Attribute a fact's evidence bbox to exactly one table cell, or don't
    attribute it at all (A6.6/A7.3 — no guessing). Requires ALL of:
    exactly one candidate table on this page containing the bbox, and
    exactly one non-ambiguous cell in that table whose own text agrees with
    value_raw. Any other outcome (no table, more than one candidate table,
    zero or multiple matching cells) returns None rather than picking."""
    if bbox is None:
        return None
    candidates = [t for t in tables if t.page_no == page_no and _bbox_within(bbox, t.bbox)]
    if len(candidates) != 1:
        return None
    table = candidates[0]
    structured = structure_table(table)
    matches = find_cells_matching_value(structured, value_raw)
    if len(matches) != 1:
        return None
    return table, matches[0]
