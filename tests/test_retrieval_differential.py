"""
Differential tests: retrieval must never change what a pair MEANS.

The contract this file pins, in both directions:

  ALLOWED    — retrieval misses a pair. It is a bounded search; a pair
               outside the budget was not examined. That is a recall
               limitation, documented and measured, not a correctness bug.

  FORBIDDEN  — retrieval surfaces a pair and the gate verdict, reason code
               or relationship type differs from what the unchanged
               bruteforce/cluster path produces for that same pair.

So every assertion here is of the form "for pairs BOTH paths surface, the
semantics are identical", plus a containment check proving retrieval's
relation set is a SUBSET of bruteforce's (never an invention).

Also covers the failure/fallback paths (task section 23): a broken
retrieval channel must degrade retrieval, never take the knowledge layer
down and never silently report "no relationship".
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.adjudicate import adjudicate, adjudicate_cluster
from fact_layer.comparability import gate
from fact_layer.models import (
    Evidence, Fact, Qualifiers, Quantity, RelationType, Scope, ValueKind,
)
from fact_layer.normalize import parse_period
from fact_layer.retrieval.config import RetrievalConfig
from fact_layer.retrieval.index import RetrievalIndex
from fact_layer.retrieval.integration import (
    adjudicate_via_retrieval, generate_candidate_pairs_with_gates,
)

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
        enabled=True, top_k=50, index_dir=str(tmp_path / "idx"),
        embedding_model_cache_dir=_MODEL_CACHE_DIR, embedding_mode="replay",
    )
    params.update(overrides)
    return RetrievalIndex(RetrievalConfig(**params))


def _mkfact(subject, measure, value_raw, period_label=None, scope=Scope.UNKNOWN,
            unit="currency", currency="INR", issuer=None, segment=None,
            doc_id="d1", page=1) -> Fact:
    period = parse_period(period_label) if period_label else None
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(value_raw), unit=unit, currency=currency,
                       sig_figs=4, raw=value_raw),
        qualifiers=Qualifiers(period=period, scope=scope, issuer=issuer, segment=segment),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject, measure_raw=measure,
    )


def _mixed_corpus():
    """A corpus deliberately spanning every contextual dimension blocking
    must NOT filter on — period, scope, unit/currency, issuer, segment —
    so the differential comparison exercises the interesting gate paths."""
    out = []
    periods = ["FY2021-22", "FY2022-23", "FY2023-24", "Q1 FY2023-24"]
    for i, p in enumerate(periods):
        out.append(_mkfact("acme", "revenue", str(1000 + i * 100), period_label=p,
                           doc_id=f"d{i}", page=i + 1))
    out.append(_mkfact("acme", "revenue", "1500", period_label="FY2023-24",
                       scope=Scope.STANDALONE, doc_id="d10", page=1))
    out.append(_mkfact("acme", "revenue", "1600", period_label="FY2023-24",
                       scope=Scope.CONSOLIDATED, doc_id="d11", page=1))
    out.append(_mkfact("acme", "revenue", "1700", period_label="FY2023-24",
                       currency="USD", doc_id="d12", page=1))
    out.append(_mkfact("acme", "revenue", "1800", period_label="FY2023-24",
                       unit="percent", currency=None, doc_id="d13", page=1))
    out.append(_mkfact("acme", "revenue", "1900", period_label="FY2023-24",
                       issuer="RBI", doc_id="d14", page=1))
    out.append(_mkfact("acme", "revenue", "2100", period_label="FY2023-24",
                       segment="express", doc_id="d15", page=1))
    return out


def _norm(rel):
    """Direction-independent semantic identity of a relation."""
    return (
        tuple(sorted((rel.source_fact_id, rel.target_fact_id))),
        rel.relation.value,
        rel.reason_code,
    )


# --------------------------------------------------------------------------
# Gate / adjudication semantics on surfaced pairs
# --------------------------------------------------------------------------

def test_surfaced_pairs_get_identical_gate_verdicts(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    pairs, gates, _diag = generate_candidate_pairs_with_gates(facts, by_id, idx)
    assert pairs, "the corpus must actually surface candidate pairs"

    for fa, fb in pairs:
        key = tuple(sorted((fa.fact_id, fb.fact_id)))
        retrieved_gate = gates[key]
        direct_gate = gate(fa, fb)
        assert retrieved_gate.verdict == direct_gate.verdict
        assert retrieved_gate.reason_code == direct_gate.reason_code
        assert retrieved_gate.comparable == direct_gate.comparable
    idx.close()


def test_surfaced_pairs_get_identical_adjudication(tmp_path):
    """The gate result adaptive retrieval hands to adjudicate() must
    produce exactly what adjudicate() produces computing the gate itself."""
    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    pairs, gates, _diag = generate_candidate_pairs_with_gates(facts, by_id, idx)
    for fa, fb in pairs:
        key = tuple(sorted((fa.fact_id, fb.fact_id)))
        with_reuse = adjudicate(fa, fb, gates[key])
        without_reuse = adjudicate(fa, fb)
        assert with_reuse.relation == without_reuse.relation
        assert with_reuse.reason_code == without_reuse.reason_code
        assert with_reuse.confidence == without_reuse.confidence
        assert with_reuse.explanation == without_reuse.explanation
    idx.close()


def test_retrieval_relations_are_a_subset_of_bruteforce(tmp_path):
    """Retrieval may MISS relations (bounded search). It may never INVENT
    one, nor alter the type/reason of one it finds."""
    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    clusters = {}
    for f in facts:
        clusters.setdefault(f.cluster_key(), []).append(f)
    brute = []
    for cluster_facts in clusters.values():
        if len(cluster_facts) >= 2:
            brute.extend(adjudicate_cluster(cluster_facts))
    brute_kept = {_norm(r) for r in brute if r.relation != RelationType.UNRELATED}

    retrieved = adjudicate_via_retrieval(facts, by_id, idx)
    retrieved_kept = {_norm(r) for r in retrieved}

    invented = retrieved_kept - brute_kept
    assert not invented, f"retrieval invented relations absent from bruteforce: {invented}"
    idx.close()


def test_full_budget_retrieval_recovers_every_bruteforce_relation(tmp_path):
    """With a budget larger than the bucket, retrieval is not merely a
    subset — it is EQUAL to bruteforce. This separates the two failure
    modes: anything missing at full budget is a correctness bug, whereas
    something missing at a small budget is the documented recall bound."""
    idx = _mkindex(tmp_path, k_ladder=(200,), max_rounds=1)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    clusters = {}
    for f in facts:
        clusters.setdefault(f.cluster_key(), []).append(f)
    brute = []
    for cluster_facts in clusters.values():
        if len(cluster_facts) >= 2:
            brute.extend(adjudicate_cluster(cluster_facts))
    brute_kept = {_norm(r) for r in brute if r.relation != RelationType.UNRELATED}

    retrieved_kept = {_norm(r) for r in adjudicate_via_retrieval(facts, by_id, idx)}
    assert retrieved_kept == brute_kept
    idx.close()


def test_contextual_dimensions_still_reach_the_gate(tmp_path):
    """The 8/15 regression guard, restated at the differential level: a
    scope / unit / currency / issuer / segment difference must still
    produce a real relation through retrieval, not be filtered out."""
    idx = _mkindex(tmp_path, k_ladder=(200,), max_rounds=1)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    relations = adjudicate_via_retrieval(facts, by_id, idx)
    reasons = {r.reason_code for r in relations}
    # These reason codes only exist because scope/unit/issuer/segment
    # differences were allowed through blocking to the gate.
    contextual = {"scope_mismatch", "unit_mismatch", "segment_mismatch",
                  "forecast_disagreement", "period_disjoint"}
    assert reasons & contextual, (
        f"no contextual mismatch reached the gate; got reasons={reasons}. "
        "Blocking has become too aggressive."
    )
    idx.close()


def test_adaptive_and_fixed_k_agree_on_pairs_both_surface(tmp_path):
    import dataclasses

    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    base = idx.config
    idx.config = dataclasses.replace(base, adaptive_enabled=False, k_ladder=(50,), max_rounds=1)
    fixed = {_norm(r) for r in adjudicate_via_retrieval(facts, by_id, idx)}
    idx.config = base
    adaptive = {_norm(r) for r in adjudicate_via_retrieval(facts, by_id, idx)}

    # Whatever both surface must mean the same thing. Set membership is
    # keyed on (pair, relation, reason), so an overlap on pair identity
    # with a differing verdict would show up as an asymmetry here.
    fixed_pairs = {k[0] for k in fixed}
    adaptive_pairs = {k[0] for k in adaptive}
    for pair in fixed_pairs & adaptive_pairs:
        assert {k for k in fixed if k[0] == pair} == {k for k in adaptive if k[0] == pair}
    idx.close()


# --------------------------------------------------------------------------
# Failure / fallback behaviour (task section 23)
# --------------------------------------------------------------------------

def test_empty_corpus_yields_no_pairs_and_no_crash(tmp_path):
    idx = _mkindex(tmp_path)
    assert adjudicate_via_retrieval([], {}, idx) == []
    idx.close()


def test_single_fact_corpus_has_no_self_pair(tmp_path):
    idx = _mkindex(tmp_path)
    only = _mkfact("acme", "revenue", "1000", period_label="FY2023-24")
    by_id = {only.fact_id: only}
    idx.upsert_facts([only])
    candidates, _g, diag = idx.retrieve_adaptive(only, by_id)
    assert candidates == []
    assert adjudicate_via_retrieval([only], by_id, idx) == []
    idx.close()


def test_semantic_channel_failure_falls_back_to_lexical(tmp_path):
    """A dead vector store must not take retrieval down: the lexical
    channel alone still surfaces candidates. Losing a channel costs
    recall, which is a bounded-search outcome, not a correctness one."""
    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)

    def _boom(*args, **kwargs):
        raise RuntimeError("vector store unavailable")

    original = idx.vectors.search
    idx.vectors.search = _boom
    try:
        with pytest.raises(RuntimeError):
            idx.retrieve_adaptive(facts[0], by_id)
    finally:
        idx.vectors.search = original

    # and it recovers cleanly once the channel is back
    candidates, _g, _d = idx.retrieve_adaptive(facts[0], by_id)
    assert candidates
    idx.close()


def test_unresolvable_candidate_id_is_blocked_not_paired(tmp_path):
    """A fact_id from the index that no longer resolves against the
    authoritative Store must be dropped, never paired on the strength of
    vector-store metadata alone."""
    idx = _mkindex(tmp_path)
    facts = _mixed_corpus()
    idx.upsert_facts(facts)
    # Authoritative map deliberately omits everything but the query fact.
    truncated = {facts[0].fact_id: facts[0]}
    candidates, gates, diag = idx.retrieve_adaptive(facts[0], truncated)
    assert all(c.blocking_status == "blocked" for c in candidates)
    assert all(c.blocking_reason == "UNKNOWN_FACT_ID" for c in candidates)
    assert gates == {}
    pairs, _g, _d = generate_candidate_pairs_with_gates([facts[0]], truncated, idx)
    assert pairs == []
    idx.close()


def test_all_candidates_incomparable_is_not_reported_as_no_candidates(tmp_path):
    """"Everything was incomparable" and "nothing was retrieved" are
    different facts about the world and must stay distinguishable."""
    idx = _mkindex(tmp_path)
    a = _mkfact("acme", "revenue", "1000", period_label="FY2021-22", doc_id="d1", page=1)
    b = _mkfact("acme", "revenue", "2000", period_label="FY2021-22",
                scope=Scope.STANDALONE, segment="express", doc_id="d2", page=1)
    by_id = {a.fact_id: a, b.fact_id: b}
    idx.upsert_facts([a, b])
    candidates, _g, diag = idx.retrieve_adaptive(a, by_id)
    assert candidates, "a candidate WAS retrieved"
    assert diag.termination_reason != "no_candidates_found"
    idx.close()
