"""
Retrieval diagnostics tests.

The diagnostics record is the only thing an evaluator sees when asking
"what happened when this fact was searched?", so every number in it must
be derived from the thing it claims to describe — gate counts from actual
`gate()` results, relationship counts from actual `adjudicate()` output,
never inferred from retrieval scores.

The counting contract is the subtle part and gets its own tests: per-channel
counts OVERLAP, so they must never be presented as summable, and
`union_unique` is the only honest "how many distinct candidates" number.
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.comparability import Verdict, gate
from fact_layer.models import (
    Evidence, Fact, Qualifiers, Quantity, Scope, ValueKind,
)
from fact_layer.normalize import parse_period
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
            unit="currency", currency="INR", doc_id="d1", page=1) -> Fact:
    period = parse_period(period_label) if period_label else None
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(value_raw), unit=unit, currency=currency,
                       sig_figs=4, raw=value_raw),
        qualifiers=Qualifiers(period=period, scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject, measure_raw=measure,
    )


def _corpus():
    """One blocking bucket, distinct periods/scopes so the gate produces a
    genuine mix of verdicts rather than all-identical ones."""
    return [
        _mkfact("acme", "revenue", "1000", period_label="FY2021-22", doc_id="d1", page=1),
        _mkfact("acme", "revenue", "2000", period_label="FY2022-23", doc_id="d2", page=1),
        _mkfact("acme", "revenue", "3000", period_label="FY2023-24", doc_id="d3", page=1),
        _mkfact("acme", "revenue", "4000", period_label="FY2023-24", scope=Scope.STANDALONE,
                doc_id="d4", page=1),
        _mkfact("acme", "revenue", "5000", period_label="FY2023-24", currency="USD",
                doc_id="d5", page=1),
    ]


# --------------------------------------------------------------------------
# Counting contract
# --------------------------------------------------------------------------

def test_channel_counts_overlap_and_are_not_summable(tmp_path):
    """lexical + semantic must NOT be presented as a total. The record
    publishes the intersection and the union explicitly so no consumer has
    to guess."""
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)

    d = diag.to_dict()["channels"]
    assert d["overlaps"] is True
    # union is bounded by the sum, and equals sum - intersection
    assert d["union_unique"] == d["lexical_unique"] + d["semantic_unique"] - d["both_channels"]
    assert d["union_unique"] <= d["lexical_unique"] + d["semantic_unique"]
    assert d["both_channels"] <= min(d["lexical_unique"], d["semantic_unique"])
    idx.close()


def test_blocked_and_unblocked_partition_the_retrieved_set(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    c = diag.to_dict()["counts"]
    assert c["blocked_unique"] + c["unblocked_unique"] == c["retrieved_unique"]
    idx.close()


def test_blocking_scope_reports_what_prefiltering_excluded(tmp_path):
    """Because blocking is a search restriction, `blocked_unique` is ~0 by
    construction. The honest measure of what blocking removed is the part
    of the corpus the query was never allowed to see."""
    idx = _mkindex(tmp_path)
    facts = _corpus() + [
        _mkfact("other_subject", "other_measure", "9", period_label="FY2023-24", doc_id="d9", page=1)
        for _ in range(1)
    ]
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    b = diag.to_dict()["blocking"]
    assert b["mode"] == "pre_filter"
    assert b["corpus_size"] == len(facts)
    assert b["bucket_size"] <= b["corpus_size"]
    assert b["excluded_by_blocking"] == b["corpus_size"] - b["bucket_size"]
    assert b["excluded_by_blocking"] >= 1      # the foreign-subject fact
    idx.close()


# --------------------------------------------------------------------------
# Gate counts come from the gate, not from retrieval scores
# --------------------------------------------------------------------------

def test_gate_counts_match_recomputing_the_gate_directly(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    query = facts[0]
    candidates, gate_results, diag = idx.retrieve_adaptive(query, by_id)

    expected = {}
    for c in candidates:
        if c.blocking_status != "candidate":
            continue
        other = by_id[c.fact_id]
        if other.fact_id == query.fact_id:
            continue
        expected[c.fact_id] = gate(query, other)

    assert set(gate_results) == set(expected)
    for fid, g in expected.items():
        assert gate_results[fid].verdict == g.verdict
        assert gate_results[fid].reason_code == g.reason_code

    assert diag.gate_evaluated == len(expected)
    assert diag.comparable == sum(1 for g in expected.values() if g.comparable)
    idx.close()


def test_gate_buckets_are_exhaustive(tmp_path):
    """comparable + relation_bearing + incomparable must account for every
    evaluated pair — no pair may fall through the classification."""
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    g = diag.to_dict()["gate"]
    assert g["comparable"] + g["relation_bearing"] + g["incomparable"] == g["evaluated"]
    assert sum(g["verdicts"].values()) == g["evaluated"]
    idx.close()


def test_temporal_succession_is_not_counted_as_incomparable(tmp_path):
    """TEMPORAL_SUCCESSION and AGGREGATION_CANDIDATE both yield real
    relations (SUPERSEDES / AGGREGATES_INTO). Filing them under
    'incomparable' would tell an evaluator the opposite of what happened."""
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, gate_results, diag = idx.retrieve_adaptive(facts[0], by_id)

    relation_bearing = {Verdict.AGGREGATION_CANDIDATE, Verdict.TEMPORAL_SUCCESSION}
    actual = sum(1 for g in gate_results.values() if g.verdict in relation_bearing)
    assert diag.relation_bearing == actual
    # and none of them leaked into the incomparable reason histogram
    for g in gate_results.values():
        if g.verdict in relation_bearing:
            assert g.reason_code not in diag.gate_reasons
    idx.close()


def test_gate_reasons_only_contain_genuinely_incomparable_reasons(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, _g, diag = idx.retrieve_adaptive(facts[0], by_id)
    assert sum(diag.gate_reasons.values()) == diag.incomparable
    idx.close()


# --------------------------------------------------------------------------
# Relationship counts come from the adjudicator
# --------------------------------------------------------------------------

def test_relationship_counts_come_from_the_adjudicator(tmp_path):
    from fact_layer.adjudicate import adjudicate
    from fact_layer.models import RelationType

    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    query = facts[0]
    candidates, diag = idx.candidate_diagnostics(query, by_id)

    expected = {}
    for c in candidates:
        if c.blocking_status != "candidate":
            continue
        other = by_id[c.fact_id]
        if other.fact_id == query.fact_id:
            continue
        if query.evidence.doc_id == other.evidence.doc_id and query.evidence.page == other.evidence.page:
            continue
        rel = adjudicate(query, other)
        if rel.relation != RelationType.UNRELATED:
            expected[rel.relation.value] = expected.get(rel.relation.value, 0) + 1

    assert diag.relationship_counts == expected
    idx.close()


def test_same_page_skips_are_reported_not_silently_dropped(tmp_path):
    """Two facts on the same page of the same document are skipped (a table
    repeating itself is not evidence). Without a count, the panel would say
    '2 candidates, 0 relationships' with no explanation."""
    idx = _mkindex(tmp_path)
    a = _mkfact("acme", "revenue", "1000", period_label="FY2021-22", doc_id="d1", page=7)
    b = _mkfact("acme", "revenue", "2000", period_label="FY2022-23", doc_id="d1", page=7)
    by_id = {a.fact_id: a, b.fact_id: b}
    idx.upsert_facts([a, b])
    _c, diag = idx.candidate_diagnostics(a, by_id)
    assert diag.same_page_skipped == 1
    assert diag.relationship_counts == {}
    idx.close()


# --------------------------------------------------------------------------
# Timing + shape
# --------------------------------------------------------------------------

def test_timing_fields_exist_and_are_non_negative(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, diag = idx.candidate_diagnostics(facts[0], by_id)
    t = diag.to_dict()["timing"]
    for key in ("lexical_ms", "semantic_ms", "fusion_ms", "blocking_ms",
                "gate_ms", "adjudication_ms", "total_ms"):
        assert key in t, f"missing timing field {key}"
        assert isinstance(t[key], (int, float))
        assert t[key] >= 0
    idx.close()


def test_diagnostics_dict_is_json_serialisable_and_complete(tmp_path):
    import json

    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, diag = idx.candidate_diagnostics(facts[0], by_id)
    d = diag.to_dict()
    json.dumps(d)       # must not raise
    for section in ("policy", "counts", "blocking", "channels", "scores",
                    "gate", "relationships", "termination", "timing"):
        assert section in d, f"missing diagnostics section {section}"
    assert d["scores"]["note"]
    assert "confidence" not in json.dumps(d["scores"]["lexical"] or {})
    idx.close()


def test_score_ranges_are_reported_as_retrieval_relevance_only(tmp_path):
    idx = _mkindex(tmp_path)
    facts = _corpus()
    by_id = {f.fact_id: f for f in facts}
    idx.upsert_facts(facts)
    _c, diag = idx.candidate_diagnostics(facts[0], by_id)
    scores = diag.to_dict()["scores"]
    assert "not comparability" in scores["note"]
    assert "not relationship confidence" in scores["note"]
    for channel in ("lexical", "semantic", "hybrid"):
        rng = scores[channel]
        if rng is not None:
            assert rng["min"] <= rng["max"]
    idx.close()
