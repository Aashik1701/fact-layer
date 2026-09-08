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
from ..models import Fact, Relation, RelationType
from .index import RetrievalIndex


def generate_candidate_pairs(
    new_facts: list[Fact], facts_by_id: dict[str, Fact], index: RetrievalIndex,
) -> list[tuple[Fact, Fact]]:
    """Retrieval-narrowed candidate pairs touching at least one fact in
    `new_facts` — the retrieval-mode analogue of
    `fact_layer.store._incremental_pairwise_relations()`'s pair-building
    half (same "only pairs touching something new" scoping), deduplicated
    by canonical pair key."""
    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[Fact, Fact]] = []
    for fact in new_facts:
        candidates = index.retrieve_candidates(fact)
        index.annotate_blocking(fact, candidates, facts_by_id)
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
    pairs = generate_candidate_pairs(new_facts, facts_by_id, index)

    out: list[Relation] = []
    for fa, fb in pairs:
        if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                and fa.evidence.page == fb.evidence.page:
            continue        # same page repetition is not evidence of anything (matches adjudicate_cluster())
        rel = adjudicate(fa, fb)
        if rel.relation != RelationType.UNRELATED:
            out.append(rel)
    return out


__all__ = ["generate_candidate_pairs", "adjudicate_via_retrieval"]
