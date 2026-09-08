"""
Adaptive retrieval policy tests.

Two halves, deliberately separated:

  * `_decide()` is a pure function of three integers, so the whole
    expansion policy is exhaustively testable with no index, no model and
    no corpus — those tests are fast and total.
  * The ladder integration tests then prove the policy is actually wired
    into `adaptive_retrieve()`: bounded K, bounded rounds, deduplication,
    determinism, and honest termination reasons.

Uses the real, already-downloaded BAAI/bge-small-en-v1.5 model
(EMBEDDING_MODE=replay, network patched off) — same contract as
test_retrieval_integration.py.
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence, Fact, Qualifiers, Quantity, Scope, ValueKind,
)
from fact_layer.normalize import parse_period
from fact_layer.retrieval.adaptive import (
    EXPAND_INSUFFICIENT_UNBLOCKED, EXPAND_SATURATED, TERMINATION_BUDGET_EXHAUSTED,
    TERMINATION_NO_CANDIDATES, TERMINATION_NO_FURTHER, TERMINATION_SUFFICIENT, _decide,
)
from fact_layer.retrieval.config import RetrievalConfig
from fact_layer.retrieval.index import RetrievalIndex

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "embeddings", "models")


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted in EMBEDDING_MODE=replay")


@pytest.fixture(autouse=True)
def _no_http(monkeypatch):
    import requests.adapters
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _no_network)
    yield


def _mkindex(tmp_path, **overrides) -> RetrievalIndex:
    params = dict(
        enabled=True, top_k=10, index_dir=str(tmp_path / "idx"),
        embedding_model_cache_dir=_MODEL_CACHE_DIR, embedding_mode="replay",
    )
    params.update(overrides)
    return RetrievalIndex(RetrievalConfig(**params))


def _mkfact(subject, measure, value_raw, period_label=None, scope=Scope.UNKNOWN,
            doc_id="d1", page=1) -> Fact:
    period = parse_period(period_label) if period_label else None
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(value_raw), unit="currency", currency="INR",
                       sig_figs=4, raw=value_raw),
        qualifiers=Qualifiers(period=period, scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject, measure_raw=measure,
    )


def _family(n: int, subject="acme", measure="revenue"):
    """`n` facts sharing one blocking bucket, each on its own doc/page so
    the same-page skip never hides them."""
    return [
        _mkfact(subject, measure, str(1_000_000 + i * 7919),
                period_label=f"FY20{10 + (i % 15)}-{11 + (i % 15)}",
                doc_id=f"d{i}", page=i + 1)
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# The policy itself — pure, exhaustive, no index required
# --------------------------------------------------------------------------

def test_partial_page_always_stops_as_exhausted():
    """Fewer results than asked for means the bucket is spent; a larger K
    cannot produce anything new, so this is exhaustion and NOT a budget
    limit — the distinction the UI depends on."""
    decision, reason = _decide(25, retrieved=9, unblocked=9, min_unblocked=5, saturation_ratio=0.9)
    assert (decision, reason) == ("stop", TERMINATION_NO_FURTHER)


def test_full_page_with_too_few_unblocked_expands():
    decision, reason = _decide(10, retrieved=10, unblocked=2, min_unblocked=5, saturation_ratio=0.9)
    assert (decision, reason) == ("expand", EXPAND_INSUFFICIENT_UNBLOCKED)


def test_full_page_that_is_saturated_expands():
    """Full page and nearly everything survived blocking: a dense
    neighbourhood whose useful region very likely continues past K."""
    decision, reason = _decide(10, retrieved=10, unblocked=10, min_unblocked=5, saturation_ratio=0.9)
    assert (decision, reason) == ("expand", EXPAND_SATURATED)


def test_full_page_with_enough_but_not_saturated_stops():
    decision, reason = _decide(10, retrieved=10, unblocked=6, min_unblocked=5, saturation_ratio=0.9)
    assert (decision, reason) == ("stop", TERMINATION_SUFFICIENT)


def test_decide_is_pure_and_deterministic():
    args = dict(min_unblocked=5, saturation_ratio=0.9)
    first = _decide(10, 10, 10, **args)
    for _ in range(50):
        assert _decide(10, 10, 10, **args) == first


def test_empty_result_stops():
    decision, reason = _decide(10, retrieved=0, unblocked=0, min_unblocked=5, saturation_ratio=0.9)
    assert decision == "stop"
    assert reason == TERMINATION_NO_FURTHER


# --------------------------------------------------------------------------
# Ladder integration
# --------------------------------------------------------------------------

def test_easy_query_stops_at_initial_k(tmp_path):
    """A small bucket returns a partial page immediately, so the ladder
    never climbs — the cheap case must stay cheap."""
    idx = _mkindex(tmp_path)
    facts = _family(4)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.final_k == diag.initial_k == 10
    assert diag.expansions == 0
    assert diag.rounds == 1
    assert diag.termination_reason == TERMINATION_NO_FURTHER
    idx.close()


def test_dense_query_expands(tmp_path):
    """A bucket larger than the first rung fills the page completely and
    everything in it survives blocking (same subject/measure/value_kind),
    which is the saturation signal."""
    idx = _mkindex(tmp_path)
    facts = _family(40)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.expansions >= 1
    assert diag.final_k > diag.initial_k
    assert EXPAND_SATURATED in diag.expansion_reasons
    idx.close()


def test_max_k_and_max_rounds_are_respected(tmp_path):
    """The hard bound. A bucket far larger than the ceiling must still
    terminate at the ceiling, report budget_exhausted, and never exceed
    the configured round count."""
    idx = _mkindex(tmp_path, k_ladder=(2, 4, 6), max_rounds=3)
    facts = _family(60)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.final_k == 6
    assert diag.final_k <= diag.max_k
    assert diag.rounds <= 3
    assert len(diag.stages) <= 3
    assert diag.termination_reason == TERMINATION_BUDGET_EXHAUSTED
    assert diag.budget_exhausted is True
    idx.close()


def test_max_rounds_truncates_a_longer_ladder(tmp_path):
    idx = _mkindex(tmp_path, k_ladder=(2, 4, 6, 8, 10), max_rounds=2)
    facts = _family(60)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.rounds <= 2
    assert diag.final_k <= 4
    idx.close()


def test_budget_exhausted_is_not_reported_as_no_relationship(tmp_path):
    """Boundedness must be visible. When the ladder runs out the record
    says so explicitly and still flags itself as a bounded search."""
    idx = _mkindex(tmp_path, k_ladder=(2,), max_rounds=1)
    facts = _family(30)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.termination_reason == TERMINATION_BUDGET_EXHAUSTED
    assert diag.bounded_search is True
    assert diag.to_dict()["termination"]["bounded_search"] is True
    idx.close()


def test_no_duplicate_candidates_across_expansion_rounds(tmp_path):
    idx = _mkindex(tmp_path, k_ladder=(5, 10, 20), max_rounds=3)
    facts = _family(40)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    candidates, gates, diag = idx.retrieve_adaptive(facts[0], by_id)
    ids = [c.fact_id for c in candidates]
    assert len(ids) == len(set(ids)), "candidate list must be deduplicated"
    assert facts[0].fact_id not in ids, "query fact must never be its own candidate"
    # gate results are memoized by candidate id, so one entry per pair max
    assert len(gates) <= len(ids)
    idx.close()


def test_adaptive_is_deterministic_across_repeated_runs(tmp_path):
    idx = _mkindex(tmp_path, k_ladder=(5, 10, 20), max_rounds=3)
    facts = _family(40)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    runs = []
    for _ in range(3):
        candidates, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
        runs.append(([c.fact_id for c in candidates], diag.final_k, diag.termination_reason))
    assert runs[0] == runs[1] == runs[2]
    idx.close()


def test_singleton_bucket_reports_no_candidates_found(tmp_path):
    """A fact with no bucket-mates surfaces nothing. The termination reason
    must say 'no candidates found', never anything implying the fact has
    been shown to be unrelated to everything."""
    idx = _mkindex(tmp_path)
    lonely = _mkfact("solo_subject", "solo_measure", "42", period_label="FY2023-24")
    others = _family(3, subject="other", measure="other_measure")
    by_id = {f.fact_id: f for f in [lonely, *others]}
    idx.upsert_facts([lonely, *others])
    candidates, _g, diag = idx.retrieve_adaptive(lonely, by_id)
    assert candidates == []
    assert diag.termination_reason == TERMINATION_NO_CANDIDATES
    assert diag.budget_exhausted is False
    idx.close()


def test_adaptive_disabled_falls_back_to_single_fixed_k(tmp_path):
    idx = _mkindex(tmp_path, adaptive_enabled=False, top_k=7)
    facts = _family(40)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert diag.policy == "fixed_k"
    assert diag.rounds == 1
    assert diag.final_k == 7
    assert diag.expansions == 0
    idx.close()


def test_ladder_k_is_monotonically_non_decreasing(tmp_path):
    idx = _mkindex(tmp_path, k_ladder=(5, 10, 20), max_rounds=3)
    facts = _family(40)
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    ks = [s.k for s in diag.stages]
    assert ks == sorted(ks)
    assert all(k <= diag.max_k for k in ks)
    idx.close()
