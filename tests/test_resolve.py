"""Tests for fact_layer/resolve.py (milestone 4, specification sections 14a/14b).

Covers all three tiers: the alias table (issuers + common measure synonyms),
fuzzy measure matching, and the capped/cached LLM fallback for the residual
ambiguous tail. The alias-collapse cases are anchored on the exact real
strings specification section 14a calls out ("International Monetary Fund" vs
"IMF staff") since that's the concrete gap this module exists to close.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from fact_layer import llm
from fact_layer.models import Evidence, Fact, Qualifiers, ValueKind
from fact_layer.normalize import normalize_entity, parse_quantity
from fact_layer.resolve import (
    ISSUER_ALIASES,
    MEASURE_ALIASES,
    Resolver,
    resolve_facts,
    write_resolution_log,
)


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/isolated cache only")


@pytest.fixture(autouse=True)
def _isolated_llm_env(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.delenv("LLM_API_KEYS", raising=False)
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    llm._stats.update(calls=0, cache_hits=0, network_calls=0, estimated_input_tokens=0,
                      repair_attempts=0, key_rotations=0)
    llm._reset_key_rotation()
    yield


def _mkfact(subject_raw, measure_raw, value_raw, issuer_raw=None, doc_id="d1", page=1) -> Fact:
    return Fact(
        subject=subject_raw.lower(), measure=measure_raw.lower().replace(" ", "_"),
        value_kind=ValueKind.QUANTITY, value=parse_quantity(value_raw),
        qualifiers=Qualifiers(issuer=issuer_raw),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject_raw, measure_raw=measure_raw,
    )


# --------------------------------------------------------------------------
# Tier 1: subjects reuse normalize_entity(), never reimplemented
# --------------------------------------------------------------------------

def test_resolve_subject_reuses_normalize_entity():
    r = Resolver()
    for raw in ["Acme Technologies Pvt. Ltd.", "ACME TECHNOLOGIES PRIVATE LIMITED"]:
        assert r.resolve_subject(raw) == normalize_entity(raw)


def test_resolve_subject_collapses_variants_to_same_canonical():
    r = Resolver()
    a = r.resolve_subject("Acme Technologies Pvt. Ltd.")
    b = r.resolve_subject("ACME TECHNOLOGIES PRIVATE LIMITED")
    assert a == b


# --------------------------------------------------------------------------
# Tier 2: issuer alias table — the exact real-world collapse specification 14a
# calls out.
# --------------------------------------------------------------------------

def test_issuer_alias_table_collapses_imf_variants():
    r = Resolver()
    assert r.resolve_issuer("International Monetary Fund") == "IMF"
    assert r.resolve_issuer("IMF staff") == "IMF"
    assert r.resolve_issuer("IMF") == "IMF"


def test_issuer_alias_table_collapses_rbi_variants():
    r = Resolver()
    assert r.resolve_issuer("Reserve Bank of India") == "RBI"
    assert r.resolve_issuer("RBI") == "RBI"


def test_issuer_alias_table_collapses_government_of_india_variants():
    r = Resolver()
    assert r.resolve_issuer("Government of India") == "Government of India"
    assert r.resolve_issuer("GoI") == "Government of India"
    assert r.resolve_issuer("Union Government") == "Government of India"


def test_issuer_with_no_alias_kept_verbatim_not_dropped():
    r = Resolver()
    unknown = "Federation of Automobile Dealers Associations (FADA)"
    assert r.resolve_issuer(unknown) == unknown


def test_issuer_none_stays_none():
    r = Resolver()
    assert r.resolve_issuer(None) is None
    assert r.resolve_issuer("") is None


# --------------------------------------------------------------------------
# Tier 2: measure alias table
# --------------------------------------------------------------------------

def test_measure_alias_table_collapses_revenue_synonyms():
    r = Resolver()
    a = r.resolve_measure("Revenue from operations")
    b = r.resolve_measure("Total income from operations")
    c = r.resolve_measure("Revenue")
    assert a == b == c


def test_measure_alias_table_collapses_gdp_growth_synonyms():
    r = Resolver()
    a = r.resolve_measure("Real GDP at Market Prices")
    b = r.resolve_measure("real GDP growth")
    assert a == b


# --------------------------------------------------------------------------
# Tier 3a: fuzzy match for near-misses not in any alias table
# --------------------------------------------------------------------------

def test_fuzzy_match_collapses_near_miss_typo():
    r = Resolver()
    a = r.resolve_measure("Gross Fiscal Deficit")
    b = r.resolve_measure("Gross Fiscal Defecit")   # typo, ratio well above 0.85
    assert a == b
    decision = r.decisions[-1]
    assert decision.tier == "fuzzy"
    assert decision.confidence >= 0.85


def test_dissimilar_measures_do_not_fuzzy_match():
    r = Resolver()
    a = r.resolve_measure("Gross Fiscal Deficit")
    b = r.resolve_measure("Net Foreign Portfolio Investment Inflows")
    assert a != b


# --------------------------------------------------------------------------
# Tier 3b: capped, cached LLM adjudication for the residual ambiguous tail
# --------------------------------------------------------------------------

def test_llm_tier_replay_miss_falls_back_gracefully_not_merged():
    """No cached response for this pair -> must not crash, must not merge."""
    r = Resolver()
    a = r.resolve_measure("Net profit margin")
    b = r.resolve_measure("Profit margin (net)")   # ratio ~0.76: below fuzzy, above ask-threshold
    assert a != b
    assert r.llm_calls_used == 1
    assert r.decisions[-1].tier == "new"


def test_llm_tier_uses_cached_response_to_merge_when_same(monkeypatch, tmp_path):
    """With a recorded cache entry saying 'same measure', the LLM tier must
    merge — proving the tier actually works, not just that it fails safe."""
    from fact_layer.resolve import _MEASURE_SCHEMA

    r = Resolver()
    r.resolve_measure("Net profit margin")   # seeds the fuzzy/LLM candidate

    provider, model, _ = llm._config()
    messages = [
        {"role": "system", "content": (
            "You are a precise financial/economic-data taxonomist. Decide "
            "whether two measure labels extracted from documents refer to "
            "the SAME underlying concept (e.g. 'Revenue from operations' "
            "and 'Total income from operations' are the same; 'Revenue' "
            "and 'Profit' are NOT, even though both are financial). "
            "Return ONLY valid JSON matching the schema."
        )},
        {"role": "user", "content": (
            f"Measure A: 'Profit margin (net)'\nMeasure B: 'Net profit margin'\n\n"
            f"Are these the same underlying measure? "
            f"Return JSON matching: {_MEASURE_SCHEMA}"
        )},
    ]
    call_messages = messages + [
        {"role": "user", "content": f"Respond with valid JSON only, matching this schema: {_MEASURE_SCHEMA}"}
    ]
    key = llm._cache_key(provider, model, call_messages)
    llm._save_cache(key, provider, model, call_messages,
                    json.dumps({"same_measure": True, "reason": "both refer to net profit margin"}))

    result = r.resolve_measure("Profit margin (net)")
    assert result == "net_profit_margin"
    assert r.decisions[-1].tier == "llm"
    assert r.llm_calls_used == 1


def test_llm_tier_respects_call_budget_cap():
    """Once the budget is exhausted, further ambiguous pairs must not
    attempt more LLM calls — they fall straight to 'new'."""
    from fact_layer.resolve import _MAX_LLM_MEASURE_CALLS

    r = Resolver()
    r.llm_calls_used = _MAX_LLM_MEASURE_CALLS   # simulate budget already spent
    r.resolve_measure("Net profit margin")
    result = r.resolve_measure("Profit margin (net)")
    assert r.llm_calls_used == _MAX_LLM_MEASURE_CALLS   # unchanged — no call attempted
    assert r.decisions[-1].tier == "new"


# --------------------------------------------------------------------------
# resolve_facts() end to end + resolution log
# --------------------------------------------------------------------------

def test_resolve_facts_updates_issuer_on_qualifiers_in_place():
    facts = [
        _mkfact("India", "real GDP growth", "6.6 percent", issuer_raw="IMF staff"),
        _mkfact("India", "Real GDP at Market Prices", "6.5", issuer_raw="Reserve Bank of India"),
    ]
    resolved, resolver = resolve_facts(facts)
    assert resolved[0].qualifiers.issuer == "IMF"
    assert resolved[1].qualifiers.issuer == "RBI"
    assert resolved[0].measure == resolved[1].measure   # both canonicalize to the alias-table id


def test_write_resolution_log_produces_valid_json(tmp_path):
    facts = [
        _mkfact("India", "real GDP growth", "6.6 percent", issuer_raw="IMF staff"),
        _mkfact("India", "Real GDP at Market Prices", "6.5", issuer_raw="Reserve Bank of India"),
    ]
    _, resolver = resolve_facts(facts)
    log_path = str(tmp_path / "resolution_log.json")
    log = write_resolution_log(resolver, path=log_path)

    assert os.path.exists(log_path)
    with open(log_path) as f:
        reloaded = json.load(f)
    assert reloaded == log
    assert log["summary"]["total_decisions"] == len(resolver.decisions)
    assert "by_tier" in log["summary"]
    assert len(log["decisions"]) == log["summary"]["total_decisions"]
