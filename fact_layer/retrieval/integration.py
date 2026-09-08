"""
Wires candidate generation into relationship adjudication, WITHOUT
changing what `fact_layer.comparability.gate()` or
`fact_layer.adjudicate.adjudicate()` decide. This module's only job is:
which pairs of facts get handed to them.

Security note (section 20): candidate fact_ids coming back from the
vector store / lexical index are treated as untrusted derived data — every
pair built here resolves both fact_ids against the caller-supplied
`facts_by_id` (which is always `Store.facts`, the authoritative map) via
`RetrievalIndex.annotate_blocking()`; a candidate whose fact_id doesn't
resolve is dropped (`blocking_status="blocked"`, never silently paired).

Pair deduplication (section 11): `pair_key = tuple(sorted((id_a, id_b)))`
so it doesn't matter whether the lexical index found A->B, the semantic
index found B->A, or both found the same pair — each unordered pair is
adjudicated at most once per call.
"""

from __future__ import annotations

from ..adjudicate import adjudicate
from ..comparability import GateResult
from ..models import Fact, Relation, RelationType
from .index import RetrievalIndex


def generate_candidate_pairs_with_gates(
    new_facts: list[Fact], facts_by_id: dict[str, Fact], index: RetrievalIndex,
) -> tuple[list[tuple[Fact, Fact]], dict[tuple[str, str], GateResult], list]:
    """Adaptive-retrieval candidate pairs, the gate result already computed
    for each, and the per-query diagnostics.

    The gate results come back because `adaptive.adaptive_retrieve()` has
    to run `gate()` anyway to report comparable/incomparable counts in its
    diagnostics. Handing them to `adjudicate(a, b, g)` means the gate is
    evaluated exactly ONCE per pair per ingest instead of twice, and — more
    importantly — that the number the diagnostics report and the number the
    adjudicator acted on are provably the same object, not two independent
    evaluations that could drift.

    Pair deduplication is keyed on the canonical unordered pair key, so a
    pair reachable from both of its facts (or re-surfaced by a later,
    larger ladder rung) is still adjudicated at most once.
    """
    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[Fact, Fact]] = []
    gates: dict[tuple[str, str], GateResult] = {}
    diagnostics = []
    for fact in new_facts:
        candidates, gate_results, diag = index.retrieve_adaptive(fact, facts_by_id)
        diagnostics.append(diag)
        for c in candidates:
            if c.blocking_status != "candidate":
                continue
            other = facts_by_id.get(c.fact_id)
            if other is None or other.fact_id == fact.fact_id:
                continue
            pair_key = tuple(sorted((fact.fact_id, other.fact_id)))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            pairs.append((fact, other))
            g = gate_results.get(c.fact_id)
            if g is not None:
                gates[pair_key] = g
    return pairs, gates, diagnostics


def generate_candidate_pairs(
    new_facts: list[Fact], facts_by_id: dict[str, Fact], index: RetrievalIndex,
) -> list[tuple[Fact, Fact]]:
    """Retrieval-narrowed candidate pairs touching at least one fact in
    `new_facts` — the retrieval-mode analogue of
    `fact_layer.store._incremental_pairwise_relations()`'s pair-building
    half (same "only pairs touching something new" scoping), deduplicated
    by canonical pair key."""
    pairs, _, _ = generate_candidate_pairs_with_gates(new_facts, facts_by_id, index)
    return pairs


def adjudicate_via_retrieval(
    new_facts: list[Fact], facts_by_id: dict[str, Fact], index: RetrievalIndex,
) -> list[Relation]:
    """relationship_mode="retrieval" path for Store.ingest(): index the new
    facts, retrieve+block candidates for each, then run the SAME
    gate()-via-adjudicate() call and same-page skip the bruteforce path
    uses — see fact_layer.adjudicate.adjudicate_cluster() /
    fact_layer.store._incremental_pairwise_relations() for the byte-for-
    byte-preserved baseline this must never diverge from in meaning."""
    index.upsert_facts(new_facts)
    pairs, gates, _diagnostics = generate_candidate_pairs_with_gates(new_facts, facts_by_id, index)

    out: list[Relation] = []
    for fa, fb in pairs:
        if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                and fa.evidence.page == fb.evidence.page:
            continue        # same page repetition is not evidence of anything (matches adjudicate_cluster())
        # Reuse the gate result adaptive retrieval already computed for
        # this exact ordered call. adjudicate() computes gate(a, b) itself
        # when g is None, so passing it changes nothing semantically — it
        # only avoids a second identical evaluation.
        g = gates.get(tuple(sorted((fa.fact_id, fb.fact_id))))
        rel = adjudicate(fa, fb, g) if g is not None else adjudicate(fa, fb)
        if rel.relation != RelationType.UNRELATED:
            out.append(rel)
    return out


__all__ = [
    "generate_candidate_pairs",
    "generate_candidate_pairs_with_gates",
    "adjudicate_via_retrieval",
]
