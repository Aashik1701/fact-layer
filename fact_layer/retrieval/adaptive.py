"""
Adaptive (bounded, self-tuning) candidate retrieval + the structured
diagnostics record that explains what a single retrieval query actually did.

WHAT "ADAPTIVE" MEANS HERE, AND WHAT IT EXPLICITLY DOES NOT MEAN
--------------------------------------------------------------------------
Adaptive retrieval changes ONE thing: how many candidates (K) are pulled
for a query fact before those candidates are handed to
`fact_layer.comparability.gate()`. It starts at a small K and climbs a
fixed ladder only when a deterministic, measurable signal says the useful
candidate region plausibly extends past the current K.

It does NOT decide comparability, contradiction, corroboration,
supersession, aggregation, relationship type, or confidence. Those remain
`comparability.gate()`'s and `adjudicate.adjudicate()`'s sole authority,
exactly as before — this module only feeds them pairs. A candidate with a
high hybrid score is not "more related"; it is only "retrieved earlier".

Every expansion decision is a pure function of integer counts this module
measured itself (how many candidates came back, how many survived
blocking). There is no LLM in this loop, and nothing here can expand
because it "wants" to find a relationship — the ladder is fixed, the round
count is capped, and the stop conditions are evaluated before, not after,
looking at what the adjudicator produced. That ordering is deliberate: it
makes confirmation bias structurally impossible rather than merely
discouraged.

BOUNDEDNESS IS A PROPERTY, NOT A LIMITATION TO HIDE
--------------------------------------------------------------------------
When the ladder is exhausted the termination reason is
`budget_exhausted`, never "nothing else exists". A pair this search never
surfaced is a pair that was not examined — NOT a pair proven unrelated.
`RetrievalDiagnostics.bounded_search` carries that distinction to the API
and the UI so no consumer can accidentally read a retrieval miss as a
semantic verdict.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from ..comparability import GateResult, Verdict, gate
from ..models import Fact, RelationType
from .blocking import block_key


# Two gate verdicts are neither "comparable" nor a dead end: adjudicate()
# turns AGGREGATION_CANDIDATE into AGGREGATES_INTO and TEMPORAL_SUCCESSION
# into SUPERSEDES. Counting them as "incomparable" would tell an evaluator
# that nine real supersession candidates were rejected, which is the
# opposite of what happened — so they get their own bucket.
_RELATION_BEARING_VERDICTS = frozenset({
    Verdict.AGGREGATION_CANDIDATE,
    Verdict.TEMPORAL_SUCCESSION,
})

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .hybrid import CandidateMatch
    from .index import RetrievalIndex


# --------------------------------------------------------------------------
# Termination / expansion vocabulary.
#
# Deliberately small and closed: the UI renders these verbatim, so a new
# string here is a product decision, not an implementation detail.
# --------------------------------------------------------------------------

TERMINATION_SUFFICIENT = "sufficient_candidates"
TERMINATION_NO_FURTHER = "no_further_candidates"
TERMINATION_BUDGET_EXHAUSTED = "budget_exhausted"
TERMINATION_NO_CANDIDATES = "no_candidates_found"

EXPAND_INSUFFICIENT_UNBLOCKED = "insufficient_unblocked_candidates"
EXPAND_SATURATED = "candidate_set_saturated"


@dataclass
class StageRecord:
    """One rung of the ladder: what was asked for, what came back, and the
    deterministic reason the policy did or did not climb further."""
    round_index: int
    k: int
    retrieved: int
    unblocked: int
    blocked: int
    new_candidates: int
    decision: str            # "expand" | "stop"
    reason: str

    def to_dict(self) -> dict:
        return {
            "round_index": self.round_index,
            "k": self.k,
            "retrieved": self.retrieved,
            "unblocked": self.unblocked,
            "blocked": self.blocked,
            "new_candidates": self.new_candidates,
            "decision": self.decision,
            "reason": self.reason,
        }


@dataclass
class RetrievalDiagnostics:
    """Everything observable about one query fact's retrieval.

    Counting contract (this matters — the UI must not add these up):
      * `lexical_unique` / `semantic_unique` are per-channel unique
        candidate counts and DO overlap; a fact found by both channels is
        counted in both.
      * `union_unique` is the deduplicated union and is the only number
        that answers "how many distinct candidates did retrieval find".
      * `both_channels` is the size of the intersection, published so the
        overlap is explicit rather than inferred.
      * `gate_evaluated` counts pairs actually passed to `gate()`, which
        is `unblocked_unique` minus self-pairs and unresolvable ids.
    """
    query_fact_id: str

    # policy
    policy: str = "adaptive_ladder"
    k_ladder: tuple[int, ...] = ()
    initial_k: int = 0
    final_k: int = 0
    max_k: int = 0
    rounds: int = 0
    expansions: int = 0
    expansion_reasons: list[str] = field(default_factory=list)
    stages: list[StageRecord] = field(default_factory=list)

    # counts
    retrieved_unique: int = 0
    blocked_unique: int = 0
    unblocked_unique: int = 0
    blocking_reasons: dict = field(default_factory=dict)

    # blocking scope. Blocking is applied as a SEARCH RESTRICTION, so the
    # honest measure of "what blocking removed" is not `blocked_unique`
    # (which is ~0 by construction — nothing incomparable is retrieved to
    # begin with) but the corpus the query was never allowed to see:
    # `excluded_by_blocking = corpus_size - bucket_size`.
    blocking_mode: str = "pre_filter"
    bucket_size: int = 0
    corpus_size: int = 0

    # channels
    lexical_unique: int = 0
    semantic_unique: int = 0
    both_channels: int = 0
    union_unique: int = 0

    # score ranges (retrieval relevance — NEVER confidence)
    lexical_score_range: Optional[tuple[float, float]] = None
    semantic_score_range: Optional[tuple[float, float]] = None
    hybrid_score_range: Optional[tuple[float, float]] = None

    # gate (authoritative)
    gate_evaluated: int = 0
    comparable: int = 0
    incomparable: int = 0
    relation_bearing: int = 0
    same_page_skipped: int = 0
    gate_reasons: dict = field(default_factory=dict)
    gate_verdicts: dict = field(default_factory=dict)

    # adjudication (authoritative)
    relationship_counts: dict = field(default_factory=dict)

    # termination
    termination_reason: str = TERMINATION_SUFFICIENT
    budget_exhausted: bool = False
    bounded_search: bool = True

    # timing (milliseconds)
    timing: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        def rng(r):
            return None if r is None else {"min": round(r[0], 6), "max": round(r[1], 6)}

        return {
            "query_fact_id": self.query_fact_id,
            "policy": {
                "name": self.policy,
                "k_ladder": list(self.k_ladder),
                "initial_k": self.initial_k,
                "final_k": self.final_k,
                "max_k": self.max_k,
                "rounds": self.rounds,
                "expansions": self.expansions,
                "expansion_reasons": list(self.expansion_reasons),
                "stages": [s.to_dict() for s in self.stages],
            },
            "counts": {
                "retrieved_unique": self.retrieved_unique,
                "blocked_unique": self.blocked_unique,
                "unblocked_unique": self.unblocked_unique,
                "gate_evaluated": self.gate_evaluated,
                "comparable": self.comparable,
                "incomparable": self.incomparable,
                "blocking_reasons": dict(self.blocking_reasons),
            },
            "blocking": {
                "mode": self.blocking_mode,
                "bucket_size": self.bucket_size,
                "corpus_size": self.corpus_size,
                "excluded_by_blocking": max(self.corpus_size - self.bucket_size, 0),
                "blocked_after_retrieval": self.blocked_unique,
                "note": (
                    "blocking is applied as a search restriction, so incomparable facts are "
                    "never retrieved rather than retrieved and discarded"
                ),
            },
            "channels": {
                "lexical_unique": self.lexical_unique,
                "semantic_unique": self.semantic_unique,
                "both_channels": self.both_channels,
                "union_unique": self.union_unique,
                "overlaps": True,
            },
            "scores": {
                "lexical": rng(self.lexical_score_range),
                "semantic": rng(self.semantic_score_range),
                "hybrid": rng(self.hybrid_score_range),
                "note": "retrieval relevance only; not comparability and not relationship confidence",
            },
            "gate": {
                "evaluated": self.gate_evaluated,
                "comparable": self.comparable,
                "relation_bearing": self.relation_bearing,
                "incomparable": self.incomparable,
                "same_page_skipped": self.same_page_skipped,
                "reasons": dict(self.gate_reasons),
                "verdicts": dict(self.gate_verdicts),
                "note": (
                    "relation_bearing = aggregation_candidate/temporal_succession: not value-comparable, "
                    "but the adjudicator still derives a relationship from them"
                ),
            },
            "relationships": dict(self.relationship_counts),
            "termination": {
                "reason": self.termination_reason,
                "budget_exhausted": self.budget_exhausted,
                "bounded_search": self.bounded_search,
            },
            "timing": dict(self.timing),
        }


def _range(values: list[float]) -> Optional[tuple[float, float]]:
    return (min(values), max(values)) if values else None


def _decide(
    k: int, retrieved: int, unblocked: int, *,
    min_unblocked: int, saturation_ratio: float,
) -> tuple[str, str]:
    """The entire expansion policy, as a pure function of three integers.

    Returns (decision, reason) where decision is "expand" or "stop".

    Kept free of Fact/index/gate objects on purpose: a policy that can only
    see counts cannot accidentally start reasoning about semantics, and it
    is exhaustively unit-testable without building an index.
    """
    if retrieved < k:
        # The bucket returned fewer than we asked for, so a larger K
        # cannot produce anything new — this is genuine exhaustion of the
        # candidate neighbourhood, not a budget limit.
        return "stop", TERMINATION_NO_FURTHER
    if unblocked < min_unblocked:
        # We asked for K, got a full K back, but too few survived blocking
        # to be confident the gate-eligible neighbours are all above the
        # cut. The useful ones may rank below K.
        return "expand", EXPAND_INSUFFICIENT_UNBLOCKED
    if retrieved > 0 and (unblocked / retrieved) >= saturation_ratio:
        # Full page AND almost everything on it survived blocking: a dense
        # neighbourhood whose useful region very likely continues past K.
        return "expand", EXPAND_SATURATED
    return "stop", TERMINATION_SUFFICIENT


def adaptive_retrieve(
    fact: Fact,
    facts_by_id: dict[str, Fact],
    index: "RetrievalIndex",
    *,
    collect_gate: bool = True,
) -> tuple[list["CandidateMatch"], dict[str, GateResult], RetrievalDiagnostics]:
    """Run the bounded adaptive ladder for one query fact.

    Returns `(candidates, gate_results, diagnostics)`:
      * `candidates` — the deduplicated, hybrid-ranked candidates at the
        final K, each annotated with its blocking status.
      * `gate_results` — `{candidate_fact_id: GateResult}` for every pair
        actually gated. Returned so the caller can hand them straight to
        `adjudicate(a, b, g)` instead of re-running the gate, which keeps
        the gate the single authority AND evaluates it exactly once per
        pair per query.
      * `diagnostics` — the full `RetrievalDiagnostics` record.

    Deduplication across rungs is inherent rather than bolted on: each
    round re-runs the same query at a larger K, so the candidate list is a
    superset keyed by fact_id, and `gate_results` memoizes by fact_id — a
    pair is never gated twice no matter how many rounds ran.
    """
    cfg = index.config
    ladder = list(cfg.k_ladder) if cfg.adaptive_enabled else [cfg.top_k]
    max_rounds = max(1, cfg.max_rounds) if cfg.adaptive_enabled else 1
    ladder = ladder[:max_rounds]

    diag = RetrievalDiagnostics(
        query_fact_id=fact.fact_id,
        policy="adaptive_ladder" if cfg.adaptive_enabled else "fixed_k",
        k_ladder=tuple(ladder),
        initial_k=ladder[0],
        max_k=ladder[-1],
    )

    t_total = time.perf_counter()
    timing = {"lexical_ms": 0.0, "semantic_ms": 0.0, "fusion_ms": 0.0,
              "blocking_ms": 0.0, "gate_ms": 0.0, "adjudication_ms": 0.0}

    candidates: list["CandidateMatch"] = []
    gate_results: dict[str, GateResult] = {}
    seen_ids: set[str] = set()
    lexical_ids: set[str] = set()
    semantic_ids: set[str] = set()
    final_k = ladder[0]
    termination = TERMINATION_SUFFICIENT

    for round_index, k in enumerate(ladder):
        final_k = k
        # Ask each channel for k + 1. A query fact retrieves ITSELF as a
        # near-perfect match, and `channel_matches_timed()` strips it after
        # the channel has already counted it — so requesting exactly k
        # would return only k-1 real candidates. That silently costs one
        # candidate of recall at every rung, and worse, makes a full bucket
        # look like a partial page (retrieved < k), which `_decide()` reads
        # as "neighbourhood exhausted" and stops on. The ladder would then
        # almost never climb.
        lex, sem, stage_timing = index.channel_matches_timed(fact, k + 1)
        timing["lexical_ms"] += stage_timing["lexical_ms"]
        timing["semantic_ms"] += stage_timing["semantic_ms"]

        t0 = time.perf_counter()
        fused = index.hybrid.fuse(lex, sem)[:k]
        timing["fusion_ms"] += (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        index.annotate_blocking(fact, fused, facts_by_id)
        timing["blocking_ms"] += (time.perf_counter() - t0) * 1000.0

        lexical_ids.update(m.fact_id for m in lex)
        semantic_ids.update(m.fact_id for m in sem)

        new_ids = [c.fact_id for c in fused if c.fact_id not in seen_ids]
        seen_ids.update(c.fact_id for c in fused)
        candidates = fused

        retrieved = len(fused)
        unblocked = sum(1 for c in fused if c.blocking_status == "candidate")
        blocked = retrieved - unblocked

        is_last = (round_index == len(ladder) - 1)
        decision, reason = _decide(
            k, retrieved, unblocked,
            min_unblocked=cfg.min_unblocked,
            saturation_ratio=cfg.saturation_ratio,
        )
        if decision == "expand" and is_last:
            # Wanted more, but the ladder is spent. Say so honestly.
            decision, reason = "stop", TERMINATION_BUDGET_EXHAUSTED

        diag.stages.append(StageRecord(
            round_index=round_index, k=k, retrieved=retrieved,
            unblocked=unblocked, blocked=blocked,
            new_candidates=len(new_ids), decision=decision, reason=reason,
        ))
        diag.rounds = round_index + 1

        if decision == "expand":
            diag.expansions += 1
            diag.expansion_reasons.append(reason)
            continue

        termination = reason
        break

    if not candidates and not seen_ids:
        termination = TERMINATION_NO_CANDIDATES

    # ---- gate the surviving candidates (authoritative, memoized) --------
    unblocked_candidates = [c for c in candidates if c.blocking_status == "candidate"]
    if collect_gate:
        t0 = time.perf_counter()
        for c in unblocked_candidates:
            other = facts_by_id.get(c.fact_id)
            if other is None or other.fact_id == fact.fact_id:
                continue
            if c.fact_id not in gate_results:
                gate_results[c.fact_id] = gate(fact, other)
        timing["gate_ms"] += (time.perf_counter() - t0) * 1000.0

    verdicts = Counter(g.verdict.value for g in gate_results.values())
    reasons = Counter(
        g.reason_code for g in gate_results.values()
        if not g.comparable and g.verdict not in _RELATION_BEARING_VERDICTS
    )

    diag.final_k = final_k
    diag.retrieved_unique = len(candidates)
    diag.unblocked_unique = len(unblocked_candidates)
    diag.blocked_unique = len(candidates) - len(unblocked_candidates)
    diag.blocking_reasons = dict(Counter(
        c.blocking_reason for c in candidates if c.blocking_status == "blocked" and c.blocking_reason
    ))
    diag.lexical_unique = len(lexical_ids)
    diag.semantic_unique = len(semantic_ids)
    diag.both_channels = len(lexical_ids & semantic_ids)
    diag.union_unique = len(lexical_ids | semantic_ids)
    diag.lexical_score_range = _range([c.lexical_score for c in candidates])
    diag.semantic_score_range = _range([c.semantic_score for c in candidates])
    diag.hybrid_score_range = _range([c.hybrid_score for c in candidates])
    diag.gate_evaluated = len(gate_results)
    diag.comparable = sum(1 for g in gate_results.values() if g.comparable)
    diag.relation_bearing = sum(
        1 for g in gate_results.values() if g.verdict in _RELATION_BEARING_VERDICTS
    )
    diag.incomparable = diag.gate_evaluated - diag.comparable - diag.relation_bearing
    diag.gate_reasons = dict(reasons)
    diag.gate_verdicts = dict(verdicts)
    diag.termination_reason = termination
    diag.budget_exhausted = (termination == TERMINATION_BUDGET_EXHAUSTED)
    try:
        diag.bucket_size = index.vectors.bucket_size(block_key(fact))
        diag.corpus_size = len(index.vectors)
    except Exception:       # diagnostics must never break retrieval
        diag.bucket_size, diag.corpus_size = 0, 0

    timing["total_ms"] = (time.perf_counter() - t_total) * 1000.0
    diag.timing = {k: round(v, 3) for k, v in timing.items()}
    return candidates, gate_results, diag


def summarize_relationships(relations) -> dict:
    """Relationship counts for the diagnostics record, taken from the
    adjudicator's actual output — never inferred from retrieval scores or
    gate verdicts."""
    counts = Counter(
        r.relation.value if isinstance(r.relation, RelationType) else str(r.relation)
        for r in relations
    )
    return dict(counts)


__all__ = [
    "RetrievalDiagnostics",
    "StageRecord",
    "adaptive_retrieve",
    "summarize_relationships",
    "TERMINATION_SUFFICIENT",
    "TERMINATION_NO_FURTHER",
    "TERMINATION_BUDGET_EXHAUSTED",
    "TERMINATION_NO_CANDIDATES",
    "EXPAND_INSUFFICIENT_UNBLOCKED",
    "EXPAND_SATURATED",
]
