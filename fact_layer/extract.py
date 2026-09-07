"""
Fact extraction with span verification.

CLAUDE.md invariant 3 — "the model reads; Python reasons". The LLM emits raw
strings only (value_raw, period_raw, scope_raw, issuer_raw, verbatim_quote).
All conversion runs through normalize.parse_quantity / parse_period /
detect_scope, which are deterministic, tested and inspectable.

Four parts, in order:
  0. Payload guard — cap any single LLM payload at 8000 chars
  1. Document metadata pass — one call per document, 6 total
  2. Block-level extraction — table prompt and prose prompt
  3. Span verifier — exact match, fuzzy snap, or reject
  4. Metrics — extraction_report.json and console summary
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import llm
from .models import (
    Evidence,
    Fact,
    Modality,
    Qualifiers,
    Quantity,
    Scope,
    ValueKind,
)
from .normalize import (
    detect_scope,
    normalize_entity,
    parse_period,
    parse_quantity,
)
from .parse import Document, Page, Table, detect_scale_context, parse_pdf
from .triage import (
    UNLIMITED_BUDGET,
    _DEMO_BUDGETS,
    batch_pages,
    default_budget,
    select_pages,
)

logger = logging.getLogger("fact_layer.extract")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")
_REJECTED_PATH = os.path.join(_DATA_DIR, "rejected_facts.jsonl")
_REPORT_PATH = os.path.join(_DATA_DIR, "extraction_report.json")

# --------------------------------------------------------------------------
# PART 0 — payload guard
#
# CLAUDE.md section 12 is explicit: "Payload cap: 8,000 chars per call. Over
# that, split into prose and table calls. Never truncate — truncation drops
# facts with no trace, which is the one failure mode the rejected-facts log
# cannot catch." Every chunk built below is therefore built by SPLITTING,
# never by slicing a string down to `cap` — a page, a prose block or even a
# single oversized table can always be divided into more pieces instead.
# --------------------------------------------------------------------------

_PAYLOAD_CAP = 8000   # chars; any single LLM payload must stay under this


def _page_payload_chars(page: Page) -> int:
    """Total chars if we send page text + serialised tables in one call."""
    cost = len(page.text)
    for t in page.tables:
        cost += len(t.to_text_block())
    return cost


_PERCENT_HEADER_RE = re.compile(r"%|per\s*cent|percent", re.I)


def _table_column_header_text(tables: list[Table]) -> str:
    """Header-row cells (first row of .rows) that plausibly carry a
    per-column unit marker — a '%'/'per cent' phrase, or a currency/scale
    phrase parse.py's own detect_scale_context() already recognises — not
    the whole header row verbatim. A wide statistical table's header is full
    of incidental English words (date ranges, 'Average', 'years') that can
    collide with normalize.py's short currency-token substring checks (e.g.
    'years' contains the literal substring 'rs', misdetecting INR currency);
    only cells that actually look like a unit are folded in."""
    markers = []
    for t in tables:
        if not t.rows:
            continue
        for cell in t.rows[0]:
            cell = (cell or "").strip()
            if cell and (_PERCENT_HEADER_RE.search(cell) or detect_scale_context(cell)):
                markers.append(cell)
    return " ".join(markers)


def _ensure_percent_word(context: str) -> str:
    """normalize.parse_quantity() (protected, unmodified) only recognises
    'percent'/'per cent' as WORDS within its context blob — it never checks
    context for a bare '%' symbol, only within the value literal itself. A
    '%' picked up from a header or measure_raw needs to be spelled out for
    that existing check to see it."""
    lower = context.lower()
    if "%" in context and "percent" not in lower and "per cent" not in lower:
        return f"{context} percent"
    return context


@dataclass
class PayloadChunk:
    """One LLM call's worth of content, possibly spanning several batched
    pages. `pages` is every page this chunk's content was drawn from — the
    span verifier must search all of them, since a batched call's
    verbatim_quote could come from any one of them."""
    page_no: int               # first/representative page_no, for logging
    prompt_type: str           # "prose" | "table" | "combined"
    content: str                # the text to send
    tables: list[Table]         # tables included (for scale_context)
    pages: list[Page]           # every source page this chunk was built from


def _split_table_block_by_rows(table: Table, cap: int) -> list[str]:
    """Split one table's rendered text block into <=cap-char pieces at row
    boundaries. Caption + scale_context are repeated on every piece so each
    one is self-contained for the LLM. Used when a single table's own block
    already exceeds the payload cap — never truncate it instead."""
    header_lines = []
    if table.caption:
        header_lines.append(table.caption.strip())
    if table.scale_context:
        header_lines.append(f"[{table.scale_context}]")
    header = "\n".join(header_lines)
    header_cost = len(header) + (1 if header else 0)

    row_lines = [" | ".join((c or "").strip() for c in row) for row in table.rows]
    if not row_lines:
        return [header] if header else [""]

    pieces: list[str] = []
    current: list[str] = [header] if header else []
    current_len = header_cost
    for line in row_lines:
        line_cost = len(line) + 1
        if current_len + line_cost > cap and len(current) > (1 if header else 0):
            pieces.append("\n".join(current))
            current = [header] if header else []
            current_len = header_cost
        current.append(line)
        current_len += line_cost
    if current:
        pieces.append("\n".join(current))
    return pieces


def _split_prose_by_chars(text: str, cap: int) -> list[str]:
    """Split prose into <=cap-char pieces on whitespace boundaries where
    possible, never mid-word, and never by silently dropping the tail."""
    if len(text) <= cap:
        return [text]
    pieces = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + cap, n)
        if end < n:
            # back off to the last whitespace so we don't split mid-word
            split_at = text.rfind(" ", start, end)
            if split_at > start:
                end = split_at
        pieces.append(text[start:end])
        start = end
        while start < n and text[start] == " ":
            start += 1
    return pieces


def _split_page_into_chunks(page: Page, cap: int = _PAYLOAD_CAP) -> list[PayloadChunk]:
    """If a single page exceeds `cap` chars, split it into prose + table
    chunks. Used both directly (a lone oversized page) and as the fallback
    inside `_split_batch_into_chunks` when a whole batch doesn't fit."""
    parts = [page.text] if page.text.strip() else []
    for t in page.tables:
        parts.append(t.to_text_block())
    combined = "\n\n".join(p for p in parts if p.strip())

    if len(combined) <= cap:
        return [PayloadChunk(
            page_no=page.page_no, prompt_type="combined",
            content=combined, tables=list(page.tables), pages=[page],
        )]

    chunks: list[PayloadChunk] = []

    # Prose chunk(s) — page text without table content, split further if the
    # prose alone still exceeds the cap.
    if page.text.strip():
        for piece in _split_prose_by_chars(page.text, cap):
            chunks.append(PayloadChunk(
                page_no=page.page_no, prompt_type="prose",
                content=piece, tables=[], pages=[page],
            ))

    # Group tables into chunks that fit under cap; a single table whose own
    # block exceeds cap is split by row instead of being dropped or cut.
    current_tables: list[Table] = []
    current_cost = 0

    def _flush() -> None:
        nonlocal current_tables, current_cost
        if not current_tables:
            return
        combined_tables = "\n\n".join(tb.to_text_block() for tb in current_tables)
        chunks.append(PayloadChunk(
            page_no=page.page_no, prompt_type="table",
            content=combined_tables, tables=list(current_tables), pages=[page],
        ))
        current_tables = []
        current_cost = 0

    for t in page.tables:
        block = t.to_text_block()
        cost = len(block)
        if cost > cap:
            _flush()
            for piece in _split_table_block_by_rows(t, cap):
                chunks.append(PayloadChunk(
                    page_no=page.page_no, prompt_type="table",
                    content=piece, tables=[t], pages=[page],
                ))
            continue
        if current_tables and current_cost + cost > cap:
            _flush()
        current_tables.append(t)
        current_cost += cost
    _flush()

    return chunks


def _split_batch_into_chunks(pages: list[Page], cap: int = _PAYLOAD_CAP) -> list[PayloadChunk]:
    """Build the LLM call(s) for one triage-batched group of pages. If the
    whole batch's content fits under `cap`, it goes in ONE call (this is the
    point of batching — a sparse deck costs one call per several slides, not
    one per page). If it doesn't fit, fall back to per-page splitting."""
    total = sum(_page_payload_chars(p) for p in pages)

    if len(pages) > 1 and total <= cap:
        parts = []
        all_tables: list[Table] = []
        for p in pages:
            page_parts = [p.text] if p.text.strip() else []
            for t in p.tables:
                page_parts.append(t.to_text_block())
                all_tables.append(t)
            if page_parts:
                parts.append(f"--- PAGE {p.page_no} ---\n" + "\n\n".join(page_parts))
        combined = "\n\n".join(parts)
        return [PayloadChunk(
            page_no=pages[0].page_no, prompt_type="combined",
            content=combined, tables=all_tables, pages=list(pages),
        )]

    # Single page, or the batch doesn't fit combined: split page-by-page.
    chunks: list[PayloadChunk] = []
    for p in pages:
        chunks.extend(_split_page_into_chunks(p, cap))
    return chunks


# --------------------------------------------------------------------------
# PART 1 — document metadata pass
# --------------------------------------------------------------------------

_METADATA_SCHEMA = """{
  "issuer": "string — who published this document",
  "reporting_entity": "string — who the document is about (may equal issuer)",
  "document_type": "string — prospectus | annual_report | earnings_presentation | policy_report | staff_report | economic_survey | ...",
  "default_currency": "string — INR | USD | null",
  "default_scale": "string — crore | lakh | million | billion | null",
  "default_scope": "string — standalone | consolidated | unknown",
  "default_period": "string — verbatim phrase or null"
}"""


def _build_metadata_prompt(pages_text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are a financial-document analyst. Extract the DEFAULT "
                "REPORTING CONTEXT from the opening pages of a document. "
                "Return ONLY valid JSON matching the schema. Do NOT guess "
                "or fabricate information — use null for any field you cannot "
                "determine from the text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Extract the default reporting context from these opening "
                f"pages:\n\n{pages_text}\n\n"
                f"Return JSON matching this schema:\n{_METADATA_SCHEMA}"
            ),
        },
    ]


def extract_document_defaults(doc: Document) -> dict:
    """One LLM call per document to extract its reporting context."""
    # Take pages 1-2, capped at 4000 chars total
    pages_text_parts = []
    for page in doc.pages[:2]:
        if page.text.strip():
            pages_text_parts.append(f"--- PAGE {page.page_no} ---\n{page.text}")
    pages_text = "\n\n".join(pages_text_parts)[:4000]

    if not pages_text.strip():
        return {
            "issuer": None, "reporting_entity": None, "document_type": None,
            "default_currency": None, "default_scale": None,
            "default_scope": "unknown", "default_period": None,
        }

    messages = _build_metadata_prompt(pages_text)
    response = llm.complete(messages, schema_hint=_METADATA_SCHEMA, max_tokens=2000)

    try:
        defaults = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Failed to parse metadata response for %s: %r", doc.filename, response[:200])
        defaults = {}

    # Normalise to expected keys with safe defaults
    return {
        "issuer": defaults.get("issuer"),
        "reporting_entity": defaults.get("reporting_entity"),
        "document_type": defaults.get("document_type"),
        "default_currency": defaults.get("default_currency"),
        "default_scale": defaults.get("default_scale"),
        "default_scope": defaults.get("default_scope", "unknown"),
        "default_period": defaults.get("default_period"),
    }


# --------------------------------------------------------------------------
# PART 2 — block-level extraction (two prompt types)
# --------------------------------------------------------------------------

_FACT_SCHEMA = """[{
  "subject_raw": "string — as written in the document",
  "measure_raw": "string — as written, e.g. 'Revenue from operations'",
  "value_raw": "string — the literal, e.g. '8,141.71' or '6.5 per cent'",
  "period_raw": "string — verbatim phrase, e.g. 'year ended March 31, 2024' — or null",
  "scope_raw": "string — verbatim, e.g. 'Consolidated' — or null",
  "issuer_raw": "string — if the text attributes the figure to a body — or null",
  "modality": "string — asserted | estimated | projected | restated",
  "verbatim_quote": "string — the exact contiguous span containing the value"
}]"""

_TABLE_SYSTEM = (
    "You are a precise financial data extractor. Extract row-wise numerical "
    "facts from the table below. Return ONLY a JSON array of fact objects.\n\n"
    "CRITICAL RULES:\n"
    "1. Emit RAW STRINGS ONLY — never compute, convert, scale, or normalise any value.\n"
    "2. value_raw must be the LITERAL number as written (e.g. '8,141.71', not 81417100).\n"
    "3. verbatim_quote must be an exact, contiguous substring from the input text.\n"
    "4. Skip page furniture, page numbers, table-of-contents entries, totals that "
    "just repeat summed rows, and any figure without both a subject and a measure.\n"
    "5. Target 5-12 meaningful facts. Precision over recall.\n"
    "6. modality: use 'asserted' for historical actuals, 'projected' for forecasts, "
    "'estimated' for estimates, 'restated' for revised/restated figures."
)

_PROSE_SYSTEM = (
    "You are a precise document analyst. Extract narrative claims from the text: "
    "status changes, forecasts, policy statements, attributions, and key "
    "numerical assertions. Return ONLY a JSON array of fact objects.\n\n"
    "CRITICAL RULES:\n"
    "1. Emit RAW STRINGS ONLY — never compute, convert, scale, or normalise any value.\n"
    "2. value_raw must be the LITERAL number/phrase as written.\n"
    "3. verbatim_quote must be an exact, contiguous substring from the input text.\n"
    "4. Skip page furniture, page numbers, and table-of-contents entries.\n"
    "5. Target 5-12 meaningful facts. Precision over recall.\n"
    "6. For percentage growth rates, GDP figures, policy rates — capture them.\n"
    "7. modality: use 'asserted' for stated facts, 'projected' for forecasts, "
    "'estimated' for estimates, 'restated' for revised figures."
)


def _build_table_prompt(content: str, scale_context: str, doc_defaults: dict) -> list[dict]:
    ctx_parts = []
    if scale_context:
        ctx_parts.append(f"Scale context: {scale_context}")
    if doc_defaults.get("default_currency"):
        ctx_parts.append(f"Default currency: {doc_defaults['default_currency']}")
    if doc_defaults.get("default_scale"):
        ctx_parts.append(f"Default scale: {doc_defaults['default_scale']}")
    if doc_defaults.get("default_period"):
        ctx_parts.append(f"Default period: {doc_defaults['default_period']}")
    ctx_block = "\n".join(ctx_parts) if ctx_parts else ""

    user_msg = f"Extract facts from this table:\n"
    if ctx_block:
        user_msg += f"\n{ctx_block}\n"
    user_msg += f"\n{content}"

    return [
        {"role": "system", "content": _TABLE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]


def _build_prose_prompt(content: str, doc_defaults: dict) -> list[dict]:
    ctx_parts = []
    if doc_defaults.get("issuer"):
        ctx_parts.append(f"Document issuer: {doc_defaults['issuer']}")
    if doc_defaults.get("default_currency"):
        ctx_parts.append(f"Default currency: {doc_defaults['default_currency']}")
    if doc_defaults.get("default_scale"):
        ctx_parts.append(f"Default scale: {doc_defaults['default_scale']}")
    if doc_defaults.get("default_period"):
        ctx_parts.append(f"Default period: {doc_defaults['default_period']}")
    ctx_block = "\n".join(ctx_parts) if ctx_parts else ""

    user_msg = f"Extract factual claims from this text:\n"
    if ctx_block:
        user_msg += f"\n{ctx_block}\n"
    user_msg += f"\n{content}"

    return [
        {"role": "system", "content": _PROSE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]


def _clean_json_response(text: str) -> str:
    """Strip markdown code block wrappers (```json ... ```) from LLM output."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _extract_from_chunk(chunk: PayloadChunk, doc_defaults: dict) -> list[dict]:
    """Send one chunk to the LLM and return the parsed raw facts."""
    # Choose prompt based on chunk type
    scale_context = ""
    if chunk.tables:
        contexts = [t.scale_context for t in chunk.tables if t.scale_context]
        scale_context = contexts[0] if contexts else ""
        # Also check doc-level default_scale
        if not scale_context and doc_defaults.get("default_scale"):
            scale_context = doc_defaults["default_scale"]

    if chunk.prompt_type == "table":
        messages = _build_table_prompt(chunk.content, scale_context, doc_defaults)
    elif chunk.prompt_type == "prose":
        messages = _build_prose_prompt(chunk.content, doc_defaults)
    else:
        # Combined — use table prompt if tables present, else prose
        if chunk.tables:
            messages = _build_table_prompt(chunk.content, scale_context, doc_defaults)
        else:
            messages = _build_prose_prompt(chunk.content, doc_defaults)

    # Reasoning models (e.g. Groq's openai/gpt-oss-120b) spend a chunk of
    # max_tokens on hidden <think>...</think> content before ever reaching
    # the JSON — llm.py strips it, but the budget has to cover both.
    response = llm.complete(messages, schema_hint=_FACT_SCHEMA, max_tokens=4000)
    cleaned = _clean_json_response(response)

    try:
        facts = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to repair truncated JSON arrays — common with free-tier token limits.
        # Find the last complete object in a truncated array.
        facts = _repair_truncated_json(cleaned)
        if facts is None:
            logger.warning("Failed to parse extraction response: %r", response[:300])
            return []

    if isinstance(facts, dict):
        # Sometimes the model wraps in {"facts": [...]}
        facts = facts.get("facts", [facts])
    if not isinstance(facts, list):
        facts = [facts]
    return facts


def _repair_truncated_json(response: str) -> Optional[list]:
    """Attempt to recover complete fact objects from a truncated JSON array.

    The free-tier model sometimes runs out of tokens mid-array. We find the
    last complete object boundary and close the array there.
    """
    response = _clean_json_response(response)
    if not response.startswith("["):
        return None

    # Find the last complete object: look for '}' followed by optional comma/whitespace
    # before the truncation point
    last_complete = -1
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(response):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_complete = i

    if last_complete <= 0:
        return None

    truncated = response[:last_complete + 1].rstrip().rstrip(",") + "]"
    try:
        result = json.loads(truncated)
        logger.info("Recovered %d facts from truncated JSON", len(result))
        return result
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# PART 3 — span verifier
# --------------------------------------------------------------------------

def _normalise_whitespace(text: str) -> str:
    """Collapse all runs of whitespace to single spaces and strip."""
    return " ".join(text.split())


def _find_exact_span(quote_norm: str, text: str, text_norm: str) -> Optional[tuple[int, int]]:
    """Find the char_start/char_end in the ORIGINAL text corresponding to
    an exact match of the normalised quote in the normalised text."""
    idx = text_norm.find(quote_norm)
    if idx == -1:
        return None

    # Map normalised index back to original text index.
    # Build mapping: for each char in text_norm, what index in text does it come from?
    orig_indices = []
    ni = 0
    in_space = False
    for oi, ch in enumerate(text):
        if ch in (' ', '\t', '\n', '\r', '\x0c', '\x0b'):
            if not in_space and orig_indices:
                orig_indices.append(oi)
                in_space = True
            continue
        else:
            in_space = False
            orig_indices.append(oi)

    if idx >= len(orig_indices):
        return None

    start_orig = orig_indices[idx]
    end_idx = idx + len(quote_norm) - 1
    if end_idx >= len(orig_indices):
        end_orig = len(text)
    else:
        end_orig = orig_indices[end_idx] + 1

    return start_orig, end_orig


def _fuzzy_find_span(quote_norm: str, text_norm: str, threshold: float = 0.92) -> Optional[tuple[int, int, float]]:
    """Find the best fuzzy match of quote_norm within text_norm.
    Returns (start, end, ratio) in normalised-text indices or None."""
    if not quote_norm or not text_norm:
        return None

    qlen = len(quote_norm)
    best_ratio = 0.0
    best_start = 0
    best_end = 0

    # Sliding window — try windows of sizes near qlen
    for window_size in range(max(1, qlen - 20), qlen + 21):
        if window_size > len(text_norm):
            continue
        for start in range(0, len(text_norm) - window_size + 1, max(1, window_size // 4)):
            candidate = text_norm[start:start + window_size]
            ratio = difflib.SequenceMatcher(None, quote_norm, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = start
                best_end = start + window_size

    if best_ratio >= threshold:
        return best_start, best_end, best_ratio
    return None


def _map_norm_span_to_original(text: str, norm_start: int, norm_end: int) -> tuple[int, int]:
    """Map a span in normalised text back to the original text."""
    orig_indices = []
    in_space = False
    for oi, ch in enumerate(text):
        if ch in (' ', '\t', '\n', '\r', '\x0c', '\x0b'):
            if not in_space and orig_indices:
                orig_indices.append(oi)
                in_space = True
            continue
        else:
            in_space = False
            orig_indices.append(oi)

    if norm_start >= len(orig_indices):
        return 0, len(text)
    start_orig = orig_indices[min(norm_start, len(orig_indices) - 1)]
    end_idx = min(norm_end - 1, len(orig_indices) - 1)
    end_orig = orig_indices[end_idx] + 1 if end_idx >= 0 else len(text)
    return start_orig, end_orig


@dataclass
class RejectedFact:
    raw_fact: dict
    page_no: int
    doc_id: str
    doc_filename: str
    reason: str
    detail: str = ""


def _append_rejected(rejected: RejectedFact, path: str = _REJECTED_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        record = {
            "raw_fact": rejected.raw_fact,
            "page_no": rejected.page_no,
            "doc_id": rejected.doc_id,
            "doc_filename": rejected.doc_filename,
            "reason": rejected.reason,
            "detail": rejected.detail,
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


@dataclass
class _SpanMatch:
    page: Page
    char_start: int
    char_end: int
    snap_reason: str = ""       # "" for an exact match, "fuzzy_snap (ratio=...)" otherwise


def _find_span_across_pages(verbatim_quote: str, pages: list[Page]) -> Optional[_SpanMatch]:
    """Find the best match for `verbatim_quote` across every candidate source
    page for this LLM call. Batched calls span several pages, so the quote
    could legitimately come from any one of them — exact match wins over
    fuzzy, and is tried on every page before fuzzy is tried on any of them.
    Pure function: no logging, no side effects, so callers can retry/inspect
    freely without polluting rejected_facts.jsonl."""
    quote_norm = _normalise_whitespace(verbatim_quote)

    for page in pages:
        text_norm = _normalise_whitespace(page.text)
        exact = _find_exact_span(quote_norm, page.text, text_norm)
        if exact:
            char_start, char_end = exact
            return _SpanMatch(page, char_start, char_end)

    best: Optional[_SpanMatch] = None
    best_ratio = 0.0
    for page in pages:
        text_norm = _normalise_whitespace(page.text)
        fuzzy = _fuzzy_find_span(quote_norm, text_norm, threshold=0.92)
        if fuzzy and fuzzy[2] > best_ratio:
            norm_start, norm_end, ratio = fuzzy
            char_start, char_end = _map_norm_span_to_original(page.text, norm_start, norm_end)
            best = _SpanMatch(page, char_start, char_end, f"fuzzy_snap (ratio={ratio:.3f})")
            best_ratio = ratio

    return best


def verify_and_build_fact(
    raw_fact: dict,
    pages: list[Page],
    doc_id: str,
    doc_filename: str,
    doc_defaults: dict,
    scale_context: str = "",
    rejected_path: str = _REJECTED_PATH,
) -> Optional[Fact]:
    """Verify a raw LLM-emitted fact against the source page(s) for the call
    that produced it, and build a Fact. `pages` is every page the originating
    LLM call was built from — a batched call spans more than one.

    Returns None if the fact is rejected (and appends to rejected_facts.jsonl).
    """
    first_page_no = pages[0].page_no
    batch_note = f" (batch pages {pages[0].page_no}-{pages[-1].page_no})" if len(pages) > 1 else ""

    subject_raw = (raw_fact.get("subject_raw") or "").strip()
    measure_raw = (raw_fact.get("measure_raw") or "").strip()
    value_raw = (raw_fact.get("value_raw") or "").strip()
    period_raw = raw_fact.get("period_raw") or ""
    scope_raw = raw_fact.get("scope_raw") or ""
    issuer_raw = raw_fact.get("issuer_raw") or ""
    modality_raw = (raw_fact.get("modality") or "asserted").strip().lower()
    verbatim_quote = (raw_fact.get("verbatim_quote") or "").strip()

    # --- rejection checks ---
    if not subject_raw:
        _append_rejected(RejectedFact(raw_fact, first_page_no, doc_id, doc_filename, "no_subject"), rejected_path)
        return None

    if not measure_raw:
        _append_rejected(RejectedFact(raw_fact, first_page_no, doc_id, doc_filename, "no_measure"), rejected_path)
        return None

    if not verbatim_quote:
        _append_rejected(RejectedFact(
            raw_fact, first_page_no, doc_id, doc_filename,
            "quote_not_found", "empty verbatim_quote"
        ), rejected_path)
        return None

    # --- span verification (across every page this call drew from) ---
    match = _find_span_across_pages(verbatim_quote, pages)
    if match is None:
        _append_rejected(RejectedFact(
            raw_fact, first_page_no, doc_id, doc_filename,
            "quote_not_found",
            f"best fuzzy ratio below 0.92 across all candidate pages{batch_note} "
            f"for quote: {verbatim_quote[:80]}..."
        ), rejected_path)
        return None

    page = match.page
    char_start, char_end = match.char_start, match.char_end
    verified = True
    if match.snap_reason:
        logger.debug("Fuzzy-snapped quote on page %d: %s", page.page_no, match.snap_reason)

    # --- parse value ---
    # Determine effective scale context: table-level > doc-level. Also fold
    # in measure_raw — some source tables (e.g. RBI's macro-indicator
    # appendix) carry the unit inline in the row label ("... (% change)")
    # rather than in a separate column header, and the LLM already copied
    # that into measure_raw; this is a per-fact signal, not a blind
    # per-table broadcast, so it is safe alongside the column-header text.
    effective_context = scale_context
    if not effective_context and doc_defaults.get("default_scale"):
        effective_context = doc_defaults["default_scale"]
    effective_context = f"{effective_context or ''} {measure_raw or ''}".strip()
    effective_context = _ensure_percent_word(effective_context)

    quantity = parse_quantity(value_raw, effective_context)

    # Check if this is a text/entity claim rather than a quantity
    is_text_claim = False
    if quantity is None:
        # Could be a text-based fact (status change, entity name, etc.)
        # Only reject if it looks like it should be numeric
        if re.search(r'\d', value_raw):
            _append_rejected(RejectedFact(
                raw_fact, page.page_no, doc_id, doc_filename,
                "unparseable_value",
                f"parse_quantity returned None for: {value_raw}"
            ), rejected_path)
            return None
        is_text_claim = True

    # --- parse period ---
    period = None
    if period_raw and period_raw.strip():
        period = parse_period(period_raw.strip())

    # Inherit document default period if none found
    if period is None and doc_defaults.get("default_period"):
        period = parse_period(doc_defaults["default_period"])

    # --- parse scope ---
    scope = Scope.UNKNOWN
    if scope_raw and scope_raw.strip():
        scope = detect_scope(scope_raw)

    # Inherit document default scope
    if scope == Scope.UNKNOWN and doc_defaults.get("default_scope"):
        scope = detect_scope(doc_defaults["default_scope"])

    # --- parse modality ---
    modality_map = {
        "asserted": Modality.ASSERTED,
        "estimated": Modality.ESTIMATED,
        "projected": Modality.PROJECTED,
        "restated": Modality.RESTATED,
    }
    modality = modality_map.get(modality_raw, Modality.ASSERTED)

    # --- build subject (with doc-level issuer fallback) ---
    issuer = issuer_raw.strip() if issuer_raw else None
    if not issuer and doc_defaults.get("issuer"):
        issuer = doc_defaults["issuer"]

    subject = normalize_entity(subject_raw) if subject_raw else subject_raw

    # --- build evidence ---
    bbox = page.bbox_for_span(char_start, char_end)
    confidence = 1.0
    if bbox is None:
        confidence = 0.8   # page-level grounding only

    # Flag qualifier_poor if no period AND no as_of
    qualifier_poor = period is None
    if qualifier_poor:
        confidence *= 0.9

    evidence = Evidence(
        doc_id=doc_id,
        page=page.page_no,
        char_start=char_start,
        char_end=char_end,
        verbatim_quote=verbatim_quote,
        bbox=bbox,
        extractor="llm",
        verified=verified,
    )

    # --- build the Fact ---
    if is_text_claim:
        fact = Fact(
            subject=subject,
            measure=measure_raw.lower().replace(" ", "_"),
            value_kind=ValueKind.TEXT,
            value=value_raw,
            qualifiers=Qualifiers(period=period, scope=scope, issuer=issuer),
            modality=modality,
            evidence=evidence,
            confidence=confidence,
            subject_raw=subject_raw,
            measure_raw=measure_raw,
        )
    else:
        fact = Fact(
            subject=subject,
            measure=measure_raw.lower().replace(" ", "_"),
            value_kind=ValueKind.QUANTITY,
            value=quantity,
            qualifiers=Qualifiers(period=period, scope=scope, issuer=issuer),
            modality=modality,
            evidence=evidence,
            confidence=confidence,
            subject_raw=subject_raw,
            measure_raw=measure_raw,
        )

    return fact


# --------------------------------------------------------------------------
# Orchestration — extract all facts from a document
# --------------------------------------------------------------------------

@dataclass
class ExtractionStats:
    doc_id: str = ""
    doc_filename: str = ""
    proposed: int = 0
    verified: int = 0
    fuzzy_snapped: int = 0
    rejected_quote_not_found: int = 0
    rejected_unparseable_value: int = 0
    rejected_no_subject: int = 0
    rejected_no_measure: int = 0
    pages_split: int = 0
    llm_calls: int = 0
    measure_counts: dict = field(default_factory=dict)


def extract_document(
    doc: Document,
    selected_page_nos: list[int],
    doc_defaults: dict,
    rejected_path: str = _REJECTED_PATH,
    char_budget: int = 6000,
) -> tuple[list[Fact], ExtractionStats]:
    """Extract facts from a single document's selected pages.

    Selected pages are first packed into triage.batch_pages() groups so a
    sparse deck costs one call per handful of slides, not one per page — this
    is the point of milestone 2's triage batching, and it must actually be
    used here rather than just imported. Each batch then goes through the
    payload guard, which may still split it further if it doesn't fit under
    the per-call cap.
    """
    stats = ExtractionStats(doc_id=doc.doc_id, doc_filename=doc.filename)
    all_facts: list[Fact] = []

    pages_by_no = {p.page_no: p for p in doc.pages}
    selected_pages = [pages_by_no[pn] for pn in selected_page_nos
                       if pn in pages_by_no and not pages_by_no[pn].image_only]

    batches = batch_pages(selected_pages, char_budget=char_budget)

    for batch_page_nos in batches:
        batch = [pages_by_no[pn] for pn in batch_page_nos]
        chunks = _split_batch_into_chunks(batch)
        if len(chunks) > 1:
            stats.pages_split += 1

        for chunk in chunks:
            stats.llm_calls += 1

            # Determine scale context from chunk tables. scale_context alone
            # (currency/magnitude phrases like "in lakhs") misses per-column
            # units that live only in the header row — e.g. a "% change"
            # column whose cells are bare numbers like "6.5" with no % sign
            # of their own (CLAUDE.md section 14b). Fold the header row text
            # in alongside it so parse_quantity() still sees the unit even
            # when value_raw doesn't repeat it.
            scale_context = ""
            if chunk.tables:
                contexts = [t.scale_context for t in chunk.tables if t.scale_context]
                scale_context = contexts[0] if contexts else ""
            column_header = _table_column_header_text(chunk.tables)
            parse_context = f"{scale_context or ''} {column_header or ''}".strip()

            raw_facts = _extract_from_chunk(chunk, doc_defaults)
            stats.proposed += len(raw_facts)

            for raw_fact in raw_facts:
                fact = verify_and_build_fact(
                    raw_fact, chunk.pages, doc.doc_id, doc.filename,
                    doc_defaults, parse_context, rejected_path,
                )
                if fact is not None:
                    all_facts.append(fact)
                    stats.verified += 1
                    if fact.evidence and fact.evidence.verified:
                        # Exact if the quote appears verbatim on ANY page this
                        # chunk drew from; otherwise it was a fuzzy snap.
                        quote_norm = _normalise_whitespace(raw_fact.get("verbatim_quote", ""))
                        if not any(quote_norm in _normalise_whitespace(p.text) for p in chunk.pages):
                            stats.fuzzy_snapped += 1

                    # Track measure counts
                    m = raw_fact.get("measure_raw", "unknown")
                    stats.measure_counts[m] = stats.measure_counts.get(m, 0) + 1

    # Count rejections by reading the rejected file
    # (we track them during the process)
    # Per-reason rejection counts are computed at the report level by reading
    # rejected_facts.jsonl back (it's the append-only source of truth).
    return all_facts, stats


# --------------------------------------------------------------------------
# PART 4 — metrics and report
# --------------------------------------------------------------------------

def _build_report(
    all_stats: list[ExtractionStats],
    total_facts: int,
    rejected_counts: dict,
    llm_stats: dict,
    metadata_calls: int = 0,
) -> dict:
    total_proposed = sum(s.proposed for s in all_stats)
    total_verified = sum(s.verified for s in all_stats)
    total_fuzzy = sum(s.fuzzy_snapped for s in all_stats)
    total_split = sum(s.pages_split for s in all_stats)
    total_calls = sum(s.llm_calls for s in all_stats)

    pass_rate = total_verified / total_proposed if total_proposed > 0 else 0.0

    per_doc = []
    for s in all_stats:
        doc_pass_rate = s.verified / s.proposed if s.proposed > 0 else 0.0
        top_measures = sorted(s.measure_counts.items(), key=lambda x: -x[1])[:10]
        per_doc.append({
            "doc_id": s.doc_id,
            "filename": s.doc_filename,
            "facts_proposed": s.proposed,
            "facts_verified": s.verified,
            "fuzzy_snapped": s.fuzzy_snapped,
            "pages_split": s.pages_split,
            "pass_rate": round(doc_pass_rate, 4),
            "top_10_measures": [{"measure": m, "count": c} for m, c in top_measures],
        })

    return {
        "summary": {
            "facts_proposed": total_proposed,
            "facts_verified": total_verified,
            "fuzzy_snapped": total_fuzzy,
            "rejected_total": total_proposed - total_verified,
            "rejected_by_reason": rejected_counts,
            "SPAN_VERIFICATION_PASS_RATE": round(pass_rate, 4),
            "pages_split": total_split,
            "llm_calls": total_calls + metadata_calls,
            "metadata_calls": metadata_calls,
            "llm_cache_hits": llm_stats.get("cache_hits", 0),
            "estimated_input_tokens": llm_stats.get("estimated_input_tokens", 0),
        },
        "per_document": per_doc,
    }


def _count_rejections(path: str = _REJECTED_PATH) -> dict:
    """Count rejected facts by reason from the JSONL file."""
    counts: dict[str, int] = {}
    if not os.path.exists(path):
        return counts
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                reason = record.get("reason", "unknown")
                counts[reason] = counts.get(reason, 0) + 1
            except json.JSONDecodeError:
                continue
    return counts


def _print_metrics(report: dict) -> None:
    s = report["summary"]
    print("\n" + "=" * 70)
    print("EXTRACTION METRICS")
    print("=" * 70)
    print(f"  Facts proposed:                {s['facts_proposed']}")
    print(f"  Facts verified:                {s['facts_verified']}")
    print(f"  Fuzzy-snapped:                 {s['fuzzy_snapped']}")
    print(f"  Rejected total:                {s['rejected_total']}")
    for reason, count in s.get("rejected_by_reason", {}).items():
        print(f"    - {reason}: {count}")
    print(f"  SPAN VERIFICATION PASS RATE:   {s['SPAN_VERIFICATION_PASS_RATE']:.2%}")
    print(f"  Batches split (payload guard): {s['pages_split']}")
    print(f"  LLM calls made:               {s['llm_calls']} "
          f"({s['metadata_calls']} metadata + {s['llm_calls'] - s['metadata_calls']} extraction)")
    print(f"  Cache hits:                    {s['llm_cache_hits']}")
    print(f"  Estimated input tokens:        {s['estimated_input_tokens']}")
    print()

    for doc in report["per_document"]:
        print(f"  [{doc['filename']}]")
        print(f"    facts: {doc['facts_verified']}/{doc['facts_proposed']} "
              f"(pass rate: {doc['pass_rate']:.2%}), "
              f"fuzzy: {doc['fuzzy_snapped']}, split: {doc['pages_split']}")
        if doc.get("top_10_measures"):
            print(f"    top measures: {', '.join(m['measure'] + '(' + str(m['count']) + ')' for m in doc['top_10_measures'][:5])}")
    print("=" * 70)


def _print_sample_facts(facts: list[Fact], n: int = 15) -> None:
    print(f"\n{'=' * 70}")
    print(f"SAMPLE FACTS ({min(n, len(facts))} of {len(facts)})")
    print(f"{'=' * 70}")

    # Spread samples across documents
    by_doc: dict[str, list[Fact]] = {}
    for f in facts:
        doc = f.evidence.doc_id if f.evidence else "unknown"
        by_doc.setdefault(doc, []).append(f)

    samples = []
    docs = list(by_doc.keys())
    idx = 0
    while len(samples) < n and idx < len(facts):
        for doc in docs:
            if len(samples) >= n:
                break
            doc_facts = by_doc[doc]
            if idx < len(doc_facts):
                samples.append(doc_facts[idx])
        idx += 1

    for i, f in enumerate(samples[:n], 1):
        val = f.value.raw if isinstance(f.value, Quantity) else str(f.value)
        period = f.qualifiers.period.label if f.qualifiers.period else "none"
        quote = f.evidence.verbatim_quote[:80] if f.evidence else "N/A"
        doc = f.evidence.doc_id[:8] if f.evidence else "?"
        print(f"  {i:2d}. [{doc}] {f.subject_raw} | {f.measure_raw} = {val}")
        print(f"      period: {period} | scope: {f.qualifiers.scope.value} | modality: {f.modality.value}")
        print(f"      quote: \"{quote}...\"")
        print()


def _print_rejected_sample(path: str = _REJECTED_PATH, n: int = 5) -> None:
    print(f"\n{'=' * 70}")
    print(f"REJECTED FACTS (first {n})")
    print(f"{'=' * 70}")

    if not os.path.exists(path):
        print("  (no rejected facts file found)")
        return

    count = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if count >= n:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                raw = record.get("raw_fact", {})
                print(f"  {count + 1}. reason: {record.get('reason')}")
                print(f"     doc: {record.get('doc_filename')} page: {record.get('page_no')}")
                print(f"     subject: {raw.get('subject_raw', 'N/A')}")
                print(f"     measure: {raw.get('measure_raw', 'N/A')}")
                print(f"     value: {raw.get('value_raw', 'N/A')}")
                print(f"     quote: \"{(raw.get('verbatim_quote') or 'N/A')[:80]}\"")
                if record.get("detail"):
                    print(f"     detail: {record['detail'][:100]}")
                print()
                count += 1
            except json.JSONDecodeError:
                continue


# --------------------------------------------------------------------------
# Main extraction pipeline
# --------------------------------------------------------------------------

def run_extraction(pdf_paths: Optional[list[str]] = None) -> tuple[list[Fact], dict]:
    """Run the full extraction pipeline over all six PDFs.

    Returns (all_facts, report_dict).
    """
    if pdf_paths is None:
        pdf_paths = sorted(glob.glob(
            os.path.join(_REPO_ROOT, "starter-datasets", "**", "*.pdf"),
            recursive=True,
        ))

    # Clear rejected file for a fresh run
    if os.path.exists(_REJECTED_PATH):
        os.remove(_REJECTED_PATH)

    all_facts: list[Fact] = []
    all_stats: list[ExtractionStats] = []
    metadata_calls = 0

    for path in pdf_paths:
        filename = os.path.basename(path)
        print(f"\n>>> Processing: {filename}")

        # Parse
        doc = parse_pdf(path)
        if doc.error:
            print(f"    SKIP (parse error): {doc.error}")
            continue

        # PART 1: Document metadata pass. extract_document_defaults() skips
        # the LLM call entirely for a document with no text on pages 1-2, so
        # count actual calls via the llm.py call counter rather than assuming
        # one per document (that assumption is what produced the earlier
        # hardcoded "+6" in the report).
        print(f"    Extracting document defaults...")
        calls_before = llm._stats["calls"]
        doc_defaults = extract_document_defaults(doc)
        metadata_calls += llm._stats["calls"] - calls_before
        print(f"    defaults: issuer={doc_defaults.get('issuer')}, "
              f"scale={doc_defaults.get('default_scale')}, "
              f"currency={doc_defaults.get('default_currency')}")

        # Triage — select pages
        budget = _DEMO_BUDGETS.get(filename)
        if budget is None:
            budget = default_budget(doc.n_pages)
        selected_nos = select_pages(doc, budget)
        print(f"    Selected {len(selected_nos)} pages (budget={budget})")

        # PART 2 + 3: Extract and verify
        facts, stats = extract_document(doc, selected_nos, doc_defaults)
        all_facts.extend(facts)
        all_stats.append(stats)

        print(f"    Extracted {stats.verified}/{stats.proposed} facts "
              f"(fuzzy: {stats.fuzzy_snapped}, split: {stats.pages_split})")

    # PART 4: Metrics
    rejected_counts = _count_rejections(_REJECTED_PATH)
    llm_stats_snapshot = dict(llm._stats)

    report = _build_report(all_stats, len(all_facts), rejected_counts, llm_stats_snapshot, metadata_calls)

    # Save report
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Print everything
    _print_metrics(report)
    _print_sample_facts(all_facts, 15)
    _print_rejected_sample(_REJECTED_PATH, 5)

    return all_facts, report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract facts from PDFs with span verification.")
    parser.add_argument("pdfs", nargs="*", help="PDF paths; defaults to all starter-dataset PDFs")
    args = parser.parse_args()

    paths = args.pdfs if args.pdfs else None
    run_extraction(paths)


if __name__ == "__main__":
    main()
