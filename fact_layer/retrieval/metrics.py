"""
Measurement helpers for retrieval quality/scale claims (sections 12-13).
Every number this module produces is computed against a real corpus and a
real index — nothing here is a hand-picked or invented figure. Used by
`scripts/retrieval_benchmark.py` and `tests/test_retrieval_integration.py`.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import Fact
from .index import RetrievalIndex
from .text import fact_to_retrieval_text


def _rank_of_target(fact: Fact, target_id: str, index: RetrievalIndex, max_k: int, channel: str) -> int | None:
    """1-indexed rank of `target_id` among the top `max_k` results for
    `fact`'s query on the given channel, or None if absent. Self-matches
    are excluded BEFORE fusion/truncation (not after) — see
    `RetrievalIndex.retrieve_candidates()`'s docstring for why a query
    fact's own near-perfect self-match would otherwise distort min-max
    normalization and silently shrink the effective candidate pool."""
    from .embeddings import embed_texts_cached

    text = fact_to_retrieval_text(fact)
    fanout = max_k + 1

    lexical_matches, semantic_matches = [], []
    if channel in ("lexical", "hybrid"):
        lexical_matches = [m for m in index.lexical.search(text, fanout) if m.fact_id != fact.fact_id]
    if channel in ("semantic", "hybrid"):
        query_vector = embed_texts_cached([text], index.provider, index.embedding_cache)[text]
        semantic_matches = [m for m in index.vectors.search(query_vector, fanout) if m.fact_id != fact.fact_id]

    if channel == "lexical":
        ids = [m.fact_id for m in lexical_matches[:max_k]]
    elif channel == "semantic":
        ids = [m.fact_id for m in semantic_matches[:max_k]]
    else:
        ids = [c.fact_id for c in index.hybrid.fuse(lexical_matches, semantic_matches)[:max_k]]

    return ids.index(target_id) + 1 if target_id in ids else None


def recall_at_k(
    known_pairs: list[tuple[str, str]],
    facts_by_id: dict[str, Fact],
    index: RetrievalIndex,
    k_values: list[int],
    channel: str = "hybrid",
) -> dict[int, float | None]:
    """Fraction of `known_pairs` (fact_id, fact_id) where the partner fact
    appears within the top-K results (either direction — A retrieving B,
    or B retrieving A, both count, since either would surface the pair for
    adjudication) for the given channel ("lexical" | "semantic" | "hybrid")."""
    max_k = max(k_values)
    hits = {k: 0 for k in k_values}
    total = 0
    for a_id, b_id in known_pairs:
        fa, fb = facts_by_id.get(a_id), facts_by_id.get(b_id)
        if fa is None or fb is None:
            continue
        total += 1
        rank_ab = _rank_of_target(fa, b_id, index, max_k, channel)
        rank_ba = _rank_of_target(fb, a_id, index, max_k, channel)
        candidates = [r for r in (rank_ab, rank_ba) if r is not None]
        best_rank = min(candidates) if candidates else None
        for k in k_values:
            if best_rank is not None and best_rank <= k:
                hits[k] += 1
    return {k: (hits[k] / total if total else None) for k in k_values}


def scale_metrics(facts: list[Fact], index: RetrievalIndex) -> dict:
    """Real, measured candidate-volume numbers for a given fact set and
    index (section 12): the baseline pair count is the CURRENT
    architecture's actual pair count (facts grouped by
    `Fact.cluster_key()`, all pairs within each cluster) — not a naive
    N² over every fact, since that is not what `relationship_mode=
    "bruteforce"` actually computes today (see fact_layer/store.py's
    cluster-dict grouping)."""
    facts_by_id = {f.fact_id: f for f in facts}
    clusters: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        clusters[f.cluster_key()].append(f)
    baseline_pairs = sum(len(v) * (len(v) - 1) // 2 for v in clusters.values())

    index.upsert_facts(facts)
    seen_pairs: set[tuple[str, str]] = set()
    blocked, retrieved = 0, 0
    for fact in facts:
        candidates = index.retrieve_candidates(fact)
        index.annotate_blocking(fact, candidates, facts_by_id)
        for c in candidates:
            other = facts_by_id.get(c.fact_id)
            if other is None or other.fact_id == fact.fact_id:
                continue
            pair_key = tuple(sorted((fact.fact_id, other.fact_id)))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            if c.blocking_status == "candidate":
                retrieved += 1
            else:
                blocked += 1

    total_considered = blocked + retrieved
    reduction_ratio = (1 - (retrieved / baseline_pairs)) if baseline_pairs else None
    return {
        "total_facts": len(facts),
        "baseline_pairs": baseline_pairs,
        "blocked_pairs": blocked,
        "retrieved_pairs": retrieved,
        "total_candidate_pairs_considered": total_considered,
        "candidate_reduction_ratio": reduction_ratio,
    }


__all__ = ["recall_at_k", "scale_metrics"]
