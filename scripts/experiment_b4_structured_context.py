"""
B4 — controlled extraction experiment: BASELINE vs STRUCTURED table context.

Isolated by design: uses its own cache directory
(cache/experiments/b4_structured_context/), never touches cache/llm/ (the
real replay cache) or any production code path. Live LLM calls only — this
script must never be run in LLM_MODE=replay expecting cache hits, since
these exact prompts (the STRUCTURED variant especially) have never been
asked before.

BASELINE reuses extract.py's real, unmodified prompt-building functions
verbatim (_build_table_prompt, _build_prose_prompt, Table.to_text_block) —
this is exactly what production sends today.

STRUCTURED replaces only the table portion of the prompt with an explicit,
per-cell-labeled rendering built from fact_layer.table_structure's
StructuredTable (row label / column header / period / unit named per cell)
— the representation A6 already computes deterministically, just not
currently fed to the LLM (see README's "Table Context Was Not Threaded
Into the LLM Prompt" limitation this experiment exists to test).

Usage:
    LLM_MODE=live python3 scripts/experiment_b4_structured_context.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer import llm
from fact_layer.extract import (
    _FACT_SCHEMA,
    _build_prose_prompt,
    _build_table_prompt,
    _clean_json_response,
    _repair_truncated_json,
    _table_column_header_text,
    extract_document_defaults,
    verify_and_build_fact,
)
from fact_layer.parse import Page, Table, parse_pdf
from fact_layer.table_structure import structure_table
from fact_layer.value_verify import ValueVerificationStatus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_PATH = os.path.join(ROOT, "starter-datasets", "delhivery", "01-delhivery-prospectus-2022-excerpt.pdf")
TARGET_PAGES = [4, 5, 9]   # real pages, each a single "combined" chunk — see selection notes in the report
EXPERIMENT_CACHE_DIR = os.path.join(ROOT, "cache", "experiments", "b4_structured_context")


def _structured_table_block(table: Table) -> str:
    """The STRUCTURED variant of one table's text: every data cell is
    labeled with its row label, column header, and period (when the header
    confidently parses as one) — the exact per-cell attribution A6/A7
    already compute deterministically, rendered here as prompt text for
    this experiment only. Never used in production (see module docstring)."""
    st = structure_table(table)
    lines = []
    if table.caption:
        lines.append(table.caption.strip())
    if table.scale_context:
        lines.append(f"[unit: {table.scale_context}]")
    if st.column_headers:
        lines.append("columns: " + " | ".join(h or "(unlabeled)" for h in st.column_headers))
    for r, row in enumerate(st.cells):
        label = st.row_labels[r] if r < len(st.row_labels) else None
        parts = []
        for c, cell in enumerate(row):
            if c == 0 and label:
                continue   # the label column itself, already named below
            if not cell.text:
                continue   # an empty cell names nothing worth asserting
            header = cell.column_header or f"column {c}"
            period_note = f", period={cell.period_label}" if cell.period_label else ""
            parts.append(f"{header}{period_note} = {cell.text}")
        if not parts:
            continue   # a wholly-empty row (common in malformed/wrapped-header
                       # table extractions) would only add noise, not signal
        row_desc = f"row \"{label}\"" if label else f"row {r}"
        lines.append(f"{row_desc}: " + "; ".join(parts))
    return "\n".join(lines)


def _build_structured_table_prompt(tables: list[Table], doc_defaults: dict) -> list[dict]:
    scale_context = ""
    contexts = [t.scale_context for t in tables if t.scale_context]
    if contexts:
        scale_context = contexts[0]
    elif doc_defaults.get("default_scale"):
        scale_context = doc_defaults["default_scale"]

    content = "\n\n".join(_structured_table_block(t) for t in tables)
    # Reuse the real _build_table_prompt() wording/rules, only swapping in
    # the structured content string instead of the flat pipe-delimited grid
    # — the system prompt (extraction rules, schema) is IDENTICAL to
    # baseline, so any difference in output is attributable to the table
    # representation, not a different instruction set.
    return _build_table_prompt(content, scale_context, doc_defaults)


def _extract_raw_facts(messages: list[dict]) -> list[dict]:
    # schema_hint matches production's real call exactly (extract.py's
    # _extract_from_chunk) so any difference in output is attributable to
    # the table representation, not a different JSON-schema instruction.
    response = llm.complete(messages, schema_hint=_FACT_SCHEMA, max_tokens=4000)
    cleaned = _clean_json_response(response)
    try:
        facts = json.loads(cleaned)
    except json.JSONDecodeError:
        facts = _repair_truncated_json(cleaned) or []
    if isinstance(facts, dict):
        facts = facts.get("facts", [facts])
    return facts if isinstance(facts, list) else [facts]


def _summarize_facts(raw_facts: list[dict], page: Page, doc_id: str, doc_filename: str,
                      doc_defaults: dict, parse_context: str) -> dict:
    accepted, rejected_reasons = [], {}
    for raw in raw_facts:
        fact = verify_and_build_fact(
            raw, [page], doc_id, doc_filename, doc_defaults, parse_context,
            rejected_path=os.path.join(EXPERIMENT_CACHE_DIR, "rejected.jsonl"),
            tables=page.tables,
        )
        if fact is None:
            # verify_and_build_fact logs to rejected.jsonl; re-derive the
            # reason from that log's last line for this summary.
            continue
        accepted.append(fact)

    with_period = sum(1 for f in accepted if f.qualifiers.period is not None)
    with_context = sum(1 for f in accepted if f.value_verification == ValueVerificationStatus.VERIFIED_WITH_CONTEXT.value)
    verified = sum(1 for f in accepted if f.value_verification in (
        ValueVerificationStatus.VERIFIED.value, ValueVerificationStatus.VERIFIED_WITH_CONTEXT.value))
    return {
        "proposed": len(raw_facts),
        "accepted": len(accepted),
        "with_period": with_period,
        "verified_with_context": with_context,
        "value_verified_total": verified,
        "facts": [
            {"subject": f.subject_raw, "measure": f.measure_raw, "value": f.value_kind.value,
             "raw_value": str(f.value.raw) if hasattr(f.value, "raw") else str(f.value),
             "period": f.qualifiers.period.label if f.qualifiers.period else None,
             "row_label": f.evidence.row_label if f.evidence else None,
             "column_header": f.evidence.column_header if f.evidence else None,
             "value_verification": f.value_verification}
            for f in accepted
        ],
    }


def run() -> None:
    os.makedirs(EXPERIMENT_CACHE_DIR, exist_ok=True)

    doc = parse_pdf(PDF_PATH)
    # Fetched BEFORE redirecting the cache dir below: this exact call is
    # already cached from the real corpus ingest (cache/llm/), so it must
    # look there to get a free cache hit rather than spend an unplanned
    # live call on a value this experiment doesn't need to re-derive.
    doc_defaults = extract_document_defaults(doc)
    print(f"doc_defaults (from real replay cache, 0 new calls): {doc_defaults}\n")
    calls_for_defaults = llm._stats["calls"]

    # Only NOW isolate this experiment's cache from the real replay cache —
    # every call from here on is either a genuine new live call or (on a
    # re-run of this script) a hit against this experiment's own cache,
    # never cache/llm/.
    llm._CACHE_DIR = EXPERIMENT_CACHE_DIR

    results = {"baseline": {}, "structured": {}}
    for page_no in TARGET_PAGES:
        page = next(p for p in doc.pages if p.page_no == page_no)
        tables = page.tables
        column_header_text = _table_column_header_text(tables)
        scale_context = next((t.scale_context for t in tables if t.scale_context), "") or doc_defaults.get("default_scale", "")
        parse_context = f"{scale_context or ''} {column_header_text or ''}".strip()

        # --- BASELINE: real, unmodified extract.py prompt path ---
        content = "\n\n".join(t.to_text_block() for t in tables)
        baseline_messages = _build_table_prompt(content, scale_context, doc_defaults)
        baseline_raw = _extract_raw_facts(baseline_messages)
        results["baseline"][page_no] = _summarize_facts(
            baseline_raw, page, doc.doc_id, doc.filename, doc_defaults, parse_context)

        # --- STRUCTURED: A6-derived per-cell labeled context ---
        structured_messages = _build_structured_table_prompt(tables, doc_defaults)
        structured_raw = _extract_raw_facts(structured_messages)
        results["structured"][page_no] = _summarize_facts(
            structured_raw, page, doc.doc_id, doc.filename, doc_defaults, parse_context)

        print(f"page {page_no}: baseline proposed/accepted="
              f"{results['baseline'][page_no]['proposed']}/{results['baseline'][page_no]['accepted']}, "
              f"structured proposed/accepted="
              f"{results['structured'][page_no]['proposed']}/{results['structured'][page_no]['accepted']}")

    out_path = os.path.join(EXPERIMENT_CACHE_DIR, "results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results written to {out_path}")
    print(f"LLM stats: {dict(llm._stats)} "
          f"(includes {calls_for_defaults} already-cached metadata call(s) counted before isolation)")


if __name__ == "__main__":
    run()
