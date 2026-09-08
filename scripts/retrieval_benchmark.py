"""
Retrieval + Scale benchmark (project task section 14).

Extends the README's existing 100-synthetic-document / 12,000-fact
stress test (see README §10 "Empirical Scale & Stress Testing") — the
SAME workload shape (100 docs, 120 facts/doc) — with the new questions
this task adds: candidate reduction, and Recall@10/25/50/100 for the
lexical-only, semantic-only and hybrid retrieval channels, benchmarked
against the preserved `relationship_mode="bruteforce"` baseline (the
project's existing cluster-dict + `adjudicate_cluster()` path,
byte-for-byte unchanged — see `fact_layer/store.py`).

Every number this script prints is measured against a real, freshly
generated corpus and a real `RetrievalIndex` — nothing here is invented.
Run it directly:

    EMBEDDING_MODE=replay python3 scripts/retrieval_benchmark.py

(EMBEDDING_MODE=replay requires the embedding model already downloaded
once via a `live` run — see README's Quickstart. Defaults to `live` if
unset, which will download the model on first run.)
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.adjudicate import adjudicate, adjudicate_cluster
from fact_layer.models import Evidence, Fact, Period, Qualifiers, Quantity, RelationType, Scope, ValueKind
from fact_layer.retrieval.config import RetrievalConfig
from fact_layer.retrieval.index import RetrievalIndex
from fact_layer.retrieval.metrics import recall_at_k

N_DOCS = 100
FACTS_PER_DOC = 120
SEED = 20240921

_SUBJECTS = [f"synthetic_company_{i}" for i in range(25)]
_MEASURES = [f"synthetic_measure_{i}" for i in range(20)]
# A handful of (subject, measure) pairs are deliberately over-represented
# across documents — the "popular line item reported by everyone" shape a
# real multi-document corpus actually has (e.g. every filing reports
# "revenue"), which is what produces genuinely large clusters worth
# stress-testing candidate reduction against.
_POPULAR_PAIRS = [(_SUBJECTS[i], _MEASURES[i]) for i in range(6)]
_PERIOD_LABELS = ["FY2021-22", "FY2022-23", "FY2023-24", "FY2024-25",
                  "Q1 FY2023-24", "Q2 FY2023-24", "Q3 FY2023-24", "Q4 FY2023-24"]


def _period_for(label: str) -> Period:
    from fact_layer.normalize import parse_period
    return parse_period(label)


_POPULAR_PAIR_PROBABILITY = 0.08   # keeps the largest clusters in the low hundreds of
                                    # facts, not the high hundreds — see generate_corpus()'s
                                    # docstring for why this matters for runtime


def _mkfact(rng: random.Random, doc_id: str, page: int, idx: int) -> Fact:
    if rng.random() < _POPULAR_PAIR_PROBABILITY:
        subject, measure = rng.choice(_POPULAR_PAIRS)
    else:
        subject, measure = rng.choice(_SUBJECTS), rng.choice(_MEASURES)
    period_label = rng.choice(_PERIOD_LABELS)
    base_value = rng.uniform(1e6, 1e9)
    # Small jitter so same-cluster facts usually corroborate/contradict
    # rather than being byte-identical duplicates every time.
    value = Decimal(str(round(base_value * rng.uniform(0.97, 1.03), 2)))
    raw = str(value)
    scope = rng.choice([Scope.STANDALONE, Scope.CONSOLIDATED, Scope.UNKNOWN, Scope.UNKNOWN])
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=value, unit="currency", currency="INR", sig_figs=6, raw=raw),
        qualifiers=Qualifiers(period=_period_for(period_label), scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(raw),
                          verbatim_quote=f"{raw}#{doc_id}#{idx}", verified=True),
        subject_raw=subject, measure_raw=measure,
    )


def generate_corpus(n_docs: int = N_DOCS, facts_per_doc: int = FACTS_PER_DOC, seed: int = SEED) -> list[Fact]:
    """A small fraction of facts (`_POPULAR_PAIR_PROBABILITY`) land on one
    of a handful of over-represented (subject, measure) pairs — the
    "every filing reports revenue" shape a real multi-document corpus
    has, which is what actually produces large clusters worth stress-
    testing candidate reduction against. This is deliberately kept modest
    (~a few hundred facts in the largest cluster, not a few thousand):
    `adjudicate_cluster()`'s pairwise cost is O(n^2) *per cluster*, so an
    over-concentrated synthetic corpus measures Python-loop overhead on a
    pathological input, not the architectural question this benchmark
    actually asks.
    """
    rng = random.Random(seed)
    facts: list[Fact] = []
    for d in range(n_docs):
        doc_id = f"synthetic_doc_{d:03d}"
        for i in range(facts_per_doc):
            facts.append(_mkfact(rng, doc_id, page=(i // 10) + 1, idx=i))
    return facts


def run_bruteforce(facts: list[Fact]) -> tuple[list, float, int]:
    """The preserved baseline: group into clusters exactly as
    Fact.cluster_key() does, run the UNCHANGED adjudicate_cluster() per
    cluster. Returns (relations, elapsed_seconds, baseline_pair_count)."""
    clusters: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        clusters[f.cluster_key()].append(f)

    baseline_pairs = sum(len(v) * (len(v) - 1) // 2 for v in clusters.values())
    t0 = time.time()
    relations = []
    for cluster_facts in clusters.values():
        if len(cluster_facts) < 2:
            continue
        relations.extend(adjudicate_cluster(cluster_facts))
    elapsed = time.time() - t0
    return relations, elapsed, baseline_pairs


def run_retrieval(facts: list[Fact], index: RetrievalIndex) -> tuple[list, float, dict, dict]:
    """Single pass: one `retrieve_candidates()` + `annotate_blocking()`
    call per fact feeds BOTH pair generation (for adjudication) and the
    candidate-volume funnel (blocked/retrieved counts) — calling
    `generate_candidate_pairs()` and `scale_metrics()` separately would
    each loop over all `facts` independently, doubling real retrieval
    cost at this benchmark's scale for no benefit (confirmed by
    profiling: retrieve_candidates() cost is real, dominated by the
    brute-force cosine search's O(N) per-query cost at N=12,000, not
    something to pay twice over)."""
    facts_by_id = {f.fact_id: f for f in facts}
    t0 = time.time()
    index.upsert_facts(facts)

    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[Fact, Fact]] = []
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
                pairs.append((fact, other))
            else:
                blocked += 1

    relations = []
    for fa, fb in pairs:
        if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                and fa.evidence.page == fb.evidence.page:
            continue
        rel = adjudicate(fa, fb)
        if rel.relation != RelationType.UNRELATED:
            relations.append(rel)
    elapsed = time.time() - t0

    clusters: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        clusters[f.cluster_key()].append(f)
    baseline_pairs = sum(len(v) * (len(v) - 1) // 2 for v in clusters.values())
    total_considered = blocked + retrieved
    reduction_ratio = (1 - (retrieved / baseline_pairs)) if baseline_pairs else None
    scale = {
        "total_facts": len(facts), "baseline_pairs": baseline_pairs,
        "blocked_pairs": blocked, "retrieved_pairs": retrieved,
        "total_candidate_pairs_considered": total_considered,
        "candidate_reduction_ratio": reduction_ratio,
    }
    return relations, elapsed, facts_by_id, scale


def _pair_keys(relations) -> set[tuple[str, str]]:
    return {tuple(sorted((r.source_fact_id, r.target_fact_id))) for r in relations}


def main() -> None:
    print(f"Generating synthetic corpus: {N_DOCS} documents x {FACTS_PER_DOC} facts/doc "
          f"= {N_DOCS * FACTS_PER_DOC} facts...")
    facts = generate_corpus()
    facts_by_id = {f.fact_id: f for f in facts}
    print(f"  Actually generated: {len(facts)} facts (some may share fact_id on rare hash "
          f"collision, per Fact.compute_id()'s documented ~0.3% ceiling)\n")

    print("=" * 78)
    print("1. BASELINE (relationship_mode=bruteforce — unchanged cluster-dict path)")
    print("=" * 78)
    bruteforce_relations, bruteforce_elapsed, baseline_pairs = run_bruteforce(facts)
    bruteforce_kept = [r for r in bruteforce_relations if r.relation != RelationType.UNRELATED]
    print(f"  Candidate pairs evaluated (all pairs within each cluster): {baseline_pairs:,}")
    print(f"  Relations kept (non-UNRELATED):                            {len(bruteforce_kept):,}")
    print(f"  Elapsed:                                                    {bruteforce_elapsed:.3f}s")

    tmpdir = tempfile.mkdtemp(prefix="retrieval_benchmark_")
    try:
        cfg = RetrievalConfig(
            enabled=True, top_k=50, index_dir=os.path.join(tmpdir, "idx"),
            embedding_model_cache_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                    "cache", "embeddings", "models"),
            embedding_mode=os.environ.get("EMBEDDING_MODE", "live"),
        )
        index = RetrievalIndex(cfg)

        print()
        print("=" * 78)
        print("2. RETRIEVAL (relationship_mode=retrieval — blocking + hybrid retrieval)")
        print("=" * 78)
        retrieval_relations, retrieval_elapsed, _, scale = run_retrieval(facts, index)
        retrieval_kept = [r for r in retrieval_relations if r.relation != RelationType.UNRELATED]
        print(f"  Baseline pairs (same definition as above):     {scale['baseline_pairs']:,}")
        print(f"  Blocked by blocking_check():                   {scale['blocked_pairs']:,}")
        print(f"  Retrieved (survived blocking, sent to gate()): {scale['retrieved_pairs']:,}")
        print(f"  Relations kept (non-UNRELATED):                {len(retrieval_kept):,}")
        reduction = scale["candidate_reduction_ratio"]
        print(f"  Candidate reduction vs baseline:               "
              f"{reduction * 100:.1f}%" if reduction is not None else "  Candidate reduction vs baseline: n/a")
        print(f"  Elapsed (index + retrieve + adjudicate):        {retrieval_elapsed:.3f}s")

        print()
        print("=" * 78)
        print("3. KNOWN-RELATION RECOVERY")
        print("   (ground truth = every relation the unchanged bruteforce/cluster path")
        print("    actually finds on this corpus — retrieval must not silently lose these)")
        print("=" * 78)
        baseline_pair_keys = _pair_keys(bruteforce_kept)
        retrieval_pair_keys = _pair_keys(retrieval_kept)
        recovered = baseline_pair_keys & retrieval_pair_keys
        print(f"  Known relation pairs (bruteforce):  {len(baseline_pair_keys):,}")
        print(f"  Recovered by retrieval mode:         {len(recovered):,}/{len(baseline_pair_keys):,} "
              f"({(len(recovered) / len(baseline_pair_keys) * 100 if baseline_pair_keys else 0):.1f}%)")

        print()
        print("=" * 78)
        print("4. RECALL@K (fraction of known pairs whose partner appears in the top-K)")
        print("=" * 78)
        k_values = [10, 25, 50, 100]
        # recall_at_k() issues a real retrieval call per (pair, direction,
        # channel) — exhaustive over every known pair is not necessary for
        # a statistically meaningful figure, so a fixed random sample keeps
        # this benchmark's runtime bounded regardless of corpus size.
        _RECALL_SAMPLE_SIZE = 300
        sample_rng = random.Random(SEED)
        recall_sample = list(baseline_pair_keys)
        if len(recall_sample) > _RECALL_SAMPLE_SIZE:
            recall_sample = sample_rng.sample(recall_sample, _RECALL_SAMPLE_SIZE)
        print(f"  Sampling {len(recall_sample):,} of {len(baseline_pair_keys):,} known pairs "
              f"(seed={SEED}) for Recall@K measurement.")
        recall_by_channel = {}
        for channel in ("lexical", "semantic", "hybrid"):
            recall = recall_at_k(recall_sample, facts_by_id, index, k_values, channel=channel)
            recall_by_channel[channel] = recall
            row = "  ".join(f"Recall@{k}={recall[k]:.3f}" if recall[k] is not None else f"Recall@{k}=n/a"
                            for k in k_values)
            print(f"  {channel:10s}  {row}")

        report = {
            "n_docs": N_DOCS, "facts_per_doc": FACTS_PER_DOC, "total_facts": len(facts),
            "bruteforce": {"baseline_pairs": baseline_pairs, "relations_kept": len(bruteforce_kept),
                          "elapsed_seconds": round(bruteforce_elapsed, 4)},
            "retrieval": {**scale, "relations_kept": len(retrieval_kept),
                         "elapsed_seconds": round(retrieval_elapsed, 4)},
            "known_relation_recovery": {
                "known_pairs": len(baseline_pair_keys), "recovered": len(recovered),
            },
            "recall_at_k": recall_by_channel,
            "recall_at_k_sample_size": len(recall_sample),
        }
        out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "retrieval_benchmark_report.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nFull report written to {out_path}")
        index.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
