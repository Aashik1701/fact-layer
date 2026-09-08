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

import dataclasses
import json
import os
import random
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
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


def run_retrieval(facts: list[Fact], index: RetrievalIndex, *, adaptive: bool) -> dict:
    """One retrieval pass over every fact, in either mode.

    `adaptive=False` pins the ladder to a single rung at the configured
    top_k, reproducing the ORIGINAL fixed-K behaviour so the two modes are
    measured against the identical index and corpus. `adaptive=True` runs
    the bounded ladder.

    Stage timings are summed from each query's own RetrievalDiagnostics —
    the same numbers the API and UI report — rather than from a separate
    instrumentation path that could drift from what production measures.
    """
    facts_by_id = {f.fact_id: f for f in facts}
    base = index.config
    index.config = dataclasses.replace(
        base,
        adaptive_enabled=adaptive,
        k_ladder=base.k_ladder if adaptive else (base.top_k,),
        max_rounds=base.max_rounds if adaptive else 1,
    )

    stage_ms = defaultdict(float)
    k_values: list[int] = []
    rounds_values: list[int] = []
    expansions = 0
    expanded_queries = 0
    termination = Counter()
    gate_evaluated = 0
    per_fact_latency_ms: list[float] = []

    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[Fact, Fact]] = []
    gates: dict[tuple[str, str], object] = {}

    t0 = time.time()
    for fact in facts:
        candidates, gate_results, diag = index.retrieve_adaptive(fact, facts_by_id)
        for key, value in diag.timing.items():
            stage_ms[key] += value
        per_fact_latency_ms.append(diag.timing.get("total_ms", 0.0))
        k_values.append(diag.final_k)
        rounds_values.append(diag.rounds)
        expansions += diag.expansions
        if diag.expansions:
            expanded_queries += 1
        termination[diag.termination_reason] += 1
        gate_evaluated += diag.gate_evaluated

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

    t_adj = time.time()
    relations = []
    for fa, fb in pairs:
        if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                and fa.evidence.page == fb.evidence.page:
            continue
        g = gates.get(tuple(sorted((fa.fact_id, fb.fact_id))))
        rel = adjudicate(fa, fb, g) if g is not None else adjudicate(fa, fb)
        if rel.relation != RelationType.UNRELATED:
            relations.append(rel)
    adjudication_seconds = time.time() - t_adj
    elapsed = time.time() - t0

    clusters: dict[str, list[Fact]] = defaultdict(list)
    for f in facts:
        clusters[f.cluster_key()].append(f)
    baseline_pairs = sum(len(v) * (len(v) - 1) // 2 for v in clusters.values())
    retrieved = len(pairs)
    reduction_ratio = (1 - (retrieved / baseline_pairs)) if baseline_pairs else None

    index.config = base
    n = max(len(facts), 1)
    return {
        "mode": "adaptive" if adaptive else "fixed_k",
        "total_facts": len(facts),
        "baseline_pairs": baseline_pairs,
        "candidate_pairs_reaching_gate": retrieved,
        "gate_evaluations": gate_evaluated,
        "candidate_reduction_ratio": reduction_ratio,
        "relations_kept": len(relations),
        "relations": relations,
        "elapsed_seconds": round(elapsed, 4),
        "adjudication_seconds": round(adjudication_seconds, 4),
        "per_fact_latency_ms_mean": round(sum(per_fact_latency_ms) / n, 4),
        "per_fact_latency_ms_p95": round(
            sorted(per_fact_latency_ms)[min(int(n * 0.95), n - 1)], 4) if per_fact_latency_ms else None,
        "average_k": round(sum(k_values) / n, 3),
        "max_k_used": max(k_values) if k_values else 0,
        "average_rounds": round(sum(rounds_values) / n, 3),
        "expansions_total": expansions,
        "queries_expanded": expanded_queries,
        "expansion_rate": round(expanded_queries / n, 4),
        "termination_reasons": dict(termination),
        "stage_ms": {k: round(v, 2) for k, v in sorted(stage_ms.items())},
    }


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
        print("2. INDEX BUILD (cold vs warm)")
        print("=" * 78)
        t0 = time.time()
        index.upsert_facts(facts, persist=False)
        index.vectors.save()
        cold_build = time.time() - t0
        print(f"  Cold build (embed + lexical + vector index):   {cold_build:.3f}s")
        t0 = time.time()
        index.upsert_facts(facts, persist=False)
        warm_build = time.time() - t0
        print(f"  Warm rebuild (embedding cache hit, re-upsert): {warm_build:.3f}s")

        print()
        print("=" * 78)
        print("3. FIXED-K RETRIEVAL (the previous behaviour: single K=50 pass)")
        print("=" * 78)
        fixed = run_retrieval(facts, index, adaptive=False)
        _print_retrieval(fixed)

        print()
        print("=" * 78)
        print("4. ADAPTIVE RETRIEVAL (bounded K ladder)")
        print("=" * 78)
        adaptive = run_retrieval(facts, index, adaptive=True)
        _print_retrieval(adaptive)
        print(f"  K ladder:                                      {list(cfg.k_ladder)}")
        print(f"  Average K:                                     {adaptive['average_k']}")
        print(f"  Max K used:                                    {adaptive['max_k_used']}")
        print(f"  Queries expanded:                              {adaptive['queries_expanded']:,} "
              f"({adaptive['expansion_rate'] * 100:.1f}%)")
        print(f"  Average rounds:                                {adaptive['average_rounds']}")
        print(f"  Termination reasons:                           {adaptive['termination_reasons']}")

        print()
        print("=" * 78)
        print("5. KNOWN-RELATION RECOVERY")
        print("   (ground truth = every relation the unchanged bruteforce/cluster path")
        print("    actually finds on this corpus — retrieval must not silently lose these)")
        print("=" * 78)
        baseline_pair_keys = _pair_keys(bruteforce_kept)
        recovery = {}
        for label, run in (("fixed_k", fixed), ("adaptive", adaptive)):
            keys = _pair_keys(run["relations"])
            recovered = baseline_pair_keys & keys
            pct = (len(recovered) / len(baseline_pair_keys) * 100) if baseline_pair_keys else 0.0
            recovery[label] = {"known_pairs": len(baseline_pair_keys),
                               "recovered": len(recovered), "pct": round(pct, 2)}
            print(f"  {label:10s} recovered {len(recovered):,}/{len(baseline_pair_keys):,} ({pct:.1f}%)")

        print()
        print("=" * 78)
        print("6. RECALL@K")
        print("   Recall@K = fraction of KNOWN pairs whose partner appears within a")
        print("   K-candidate budget. It is NOT a claim about relationships in general:")
        print("   retrieval is a bounded search, so a miss means 'not examined within")
        print("   budget', never 'proven unrelated'.")
        print("=" * 78)
        k_values = [10, 25, 50, 100]
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

        print()
        print("=" * 78)
        print("7. SUMMARY")
        print("=" * 78)
        print(f"  {'metric':38s} {'bruteforce':>14s} {'fixed-K':>14s} {'adaptive':>14s}")
        def row(name, a, b, c):
            print(f"  {name:38s} {a:>14} {b:>14} {c:>14}")
        row("candidate pairs reaching gate", f"{baseline_pairs:,}",
            f"{fixed['candidate_pairs_reaching_gate']:,}", f"{adaptive['candidate_pairs_reaching_gate']:,}")
        row("relations kept", f"{len(bruteforce_kept):,}",
            f"{fixed['relations_kept']:,}", f"{adaptive['relations_kept']:,}")
        row("known-relation recovery", "100.0%",
            f"{recovery['fixed_k']['pct']}%", f"{recovery['adaptive']['pct']}%")
        row("end-to-end seconds", f"{bruteforce_elapsed:.2f}",
            f"{fixed['elapsed_seconds']:.2f}", f"{adaptive['elapsed_seconds']:.2f}")
        row("per-fact latency ms (mean)", "-",
            f"{fixed['per_fact_latency_ms_mean']:.3f}", f"{adaptive['per_fact_latency_ms_mean']:.3f}")

        report = {
            "n_docs": N_DOCS, "facts_per_doc": FACTS_PER_DOC, "total_facts": len(facts),
            "seed": SEED,
            "index_build": {"cold_seconds": round(cold_build, 4), "warm_seconds": round(warm_build, 4)},
            "bruteforce": {"baseline_pairs": baseline_pairs, "relations_kept": len(bruteforce_kept),
                           "elapsed_seconds": round(bruteforce_elapsed, 4)},
            "fixed_k": {k: v for k, v in fixed.items() if k != "relations"},
            "adaptive": {k: v for k, v in adaptive.items() if k != "relations"},
            "known_relation_recovery": recovery,
            "recall_at_k": recall_by_channel,
            "recall_at_k_sample_size": len(recall_sample),
            "recall_definition": (
                "fraction of known pairs surfaced within a K-candidate budget; "
                "a miss means not examined within budget, never proven unrelated"
            ),
        }
        out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "retrieval_benchmark_report.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nFull report written to {out_path}")
        index.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _print_retrieval(run: dict) -> None:
    print(f"  Baseline pairs (cluster-dict definition):      {run['baseline_pairs']:,}")
    print(f"  Candidate pairs reaching gate():               {run['candidate_pairs_reaching_gate']:,}")
    print(f"  Gate evaluations:                              {run['gate_evaluations']:,}")
    reduction = run["candidate_reduction_ratio"]
    if reduction is not None:
        print(f"  Candidate reduction vs baseline:               {reduction * 100:.1f}%")
    print(f"  Relations kept (non-UNRELATED):                {run['relations_kept']:,}")
    print(f"  Elapsed (retrieve + gate + adjudicate):        {run['elapsed_seconds']:.3f}s")
    print(f"  Per-fact latency mean / p95 (ms):              "
          f"{run['per_fact_latency_ms_mean']:.3f} / {run['per_fact_latency_ms_p95']:.3f}")
    print(f"  Stage breakdown (ms, summed over all queries): {run['stage_ms']}")


if __name__ == "__main__":
    main()
