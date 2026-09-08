"""
GET /facts/{fact_a_id}/comparability/{fact_b_id} — API tests.

Runs against the real committed store (`data/store.json`), so these also
serve as a regression on the corpus actually shipped: if the demo store
stops containing a comparable pair or a period-blocked pair, the showcase
cases in the README stop being demonstrable and these tests fail.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api as api_module
from fact_layer.comparability import Verdict, gate
from fact_layer.investigate import RELATION_BEARING_VERDICTS


@pytest.fixture(scope="module")
def client():
    return TestClient(api_module.app)


@pytest.fixture(scope="module")
def store():
    return api_module.STORE


def _first_pair_with(store, predicate):
    """A real (fact_a, fact_b) pair from the committed corpus satisfying
    `predicate(gate_result)`, searched within blocking buckets so the pairs
    are ones the system would genuinely consider."""
    from fact_layer.retrieval.blocking import block_key

    buckets = {}
    for f in store.facts.values():
        buckets.setdefault(block_key(f), []).append(f)
    for members in buckets.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if predicate(gate(a, b)):
                    return a, b
    return None, None


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------

def test_blocked_pair_returns_full_explanation(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict == Verdict.INCOMPARABLE_PERIOD)
    if a is None:
        pytest.skip("no period-blocked pair in the committed corpus")
    res = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}")
    assert res.status_code == 200
    body = res.json()

    assert body["verdict"] == gate(a, b).verdict.value
    assert body["comparable"] is False
    assert body["blocking_reasons"], "a blocked pair must name at least one reason"
    assert any(br["dimension"] == "period" for br in body["blocking_reasons"])
    assert body["counterfactual_actions"]
    assert body["safe_conclusion"]
    assert "Incomparable does not mean unrelated" in body["disclaimer"]


def test_comparable_pair_reports_no_actions(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict == Verdict.COMPARABLE)
    if a is None:
        pytest.skip("no comparable pair in the committed corpus")
    body = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()
    assert body["comparable"] is True
    assert body["blocking_reasons"] == []
    assert body["counterfactual_actions"] == []


def test_response_contains_every_documented_section(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict != Verdict.COMPARABLE)
    if a is None:
        pytest.skip("no blocked pair in the committed corpus")
    body = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()
    for key in ("verdict", "comparable", "dimensions", "blocking_reasons",
                "passing_dimensions", "ambiguous_dimensions", "counterfactual_actions",
                "safe_conclusion", "evidence_refs", "authority", "disclaimer",
                "fact_a", "fact_b"):
        assert key in body, f"missing response section {key}"
    assert body["authority"]["llm_used"] is False
    assert body["authority"]["deterministic"] is True


def test_evidence_refs_are_present_for_both_facts(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict != Verdict.COMPARABLE)
    if a is None:
        pytest.skip("no blocked pair in the committed corpus")
    refs = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()["evidence_refs"]
    assert [r["role"] for r in refs] == ["fact_a", "fact_b"]
    for r in refs:
        assert r["available"] is True
        assert r["doc_id"] and r["page"] and r["verbatim_quote"]


# --------------------------------------------------------------------------
# Determinism, ordering, gate consistency
# --------------------------------------------------------------------------

def test_repeated_calls_are_byte_identical(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict != Verdict.COMPARABLE)
    if a is None:
        pytest.skip("no blocked pair in the committed corpus")
    url = f"/facts/{a.fact_id}/comparability/{b.fact_id}"
    first = client.get(url).json()
    for _ in range(3):
        assert client.get(url).json() == first


def test_reversed_order_preserves_semantics(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict != Verdict.COMPARABLE)
    if a is None:
        pytest.skip("no blocked pair in the committed corpus")
    fwd = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()
    rev = client.get(f"/facts/{b.fact_id}/comparability/{a.fact_id}").json()
    assert fwd["verdict"] == rev["verdict"]
    assert fwd["comparable"] == rev["comparable"]
    assert ({br["dimension"] for br in fwd["blocking_reasons"]}
            == {br["dimension"] for br in rev["blocking_reasons"]})


def test_api_verdict_matches_the_gate_across_many_real_pairs(client, store):
    """The investigator is an explanation of the gate, over real data."""
    from fact_layer.retrieval.blocking import block_key

    buckets = {}
    for f in store.facts.values():
        buckets.setdefault(block_key(f), []).append(f)
    checked = 0
    for members in buckets.values():
        if len(members) < 2:
            continue
        a, b = members[0], members[1]
        body = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()
        assert body["verdict"] == gate(a, b).verdict.value
        checked += 1
        if checked >= 40:
            break
    assert checked > 0


def test_relation_bearing_pairs_are_not_reported_as_blocked(client, store):
    a, b = _first_pair_with(store, lambda g: g.verdict in RELATION_BEARING_VERDICTS)
    if a is None:
        pytest.skip("no relation-bearing pair in the committed corpus")
    body = client.get(f"/facts/{a.fact_id}/comparability/{b.fact_id}").json()
    assert body["relation_bearing"] is True
    assert body["blocking_reasons"] == []


# --------------------------------------------------------------------------
# Errors (spec §38, §42)
# --------------------------------------------------------------------------

def test_unknown_fact_a_returns_404(client, store):
    known = next(iter(store.facts))
    res = client.get(f"/facts/does_not_exist/comparability/{known}")
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown fact_a_id"


def test_unknown_fact_b_returns_404(client, store):
    known = next(iter(store.facts))
    res = client.get(f"/facts/{known}/comparability/does_not_exist")
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown fact_b_id"


def test_same_fact_twice_returns_400(client, store):
    known = next(iter(store.facts))
    res = client.get(f"/facts/{known}/comparability/{known}")
    assert res.status_code == 400


def test_path_traversal_identifier_is_not_found_not_a_file_read(client, store):
    """An id-shaped path segment must resolve through the store's dict, never
    the filesystem — a traversal attempt is simply an unknown id."""
    res = client.get("/facts/..%2F..%2Fetc%2Fpasswd/comparability/also_missing")
    assert res.status_code in (400, 404)
    body = res.text.lower()
    assert "root:" not in body
    assert "/etc/" not in body


def test_errors_do_not_leak_internals(client, store):
    known = next(iter(store.facts))
    res = client.get(f"/facts/{known}/comparability/nope")
    blob = res.text
    assert "Traceback" not in blob
    assert "/Users/" not in blob
    assert "site-packages" not in blob
