"""
GET /entities/{subject}/history — API tests.

Same minimal pattern as test_graph_api.py: importing api.py and reading
STORE triggers no LLM/network call, so no replay-mode fixture is needed.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api as api_module


@pytest.fixture(scope="module")
def client():
    return TestClient(api_module.app)


@pytest.fixture(scope="module")
def store():
    return api_module.STORE


@pytest.fixture(scope="module")
def multi_period_subject_measure(store):
    """A real (subject, measure) with 3+ facts — same pair the domain
    tests use, verified present in the committed corpus."""
    return "total income", "revenue"


# --------------------------------------------------------------------------
# Discovery: measure omitted
# --------------------------------------------------------------------------

def test_measure_omitted_returns_available_measures(client, multi_period_subject_measure):
    subject, _measure = multi_period_subject_measure
    res = client.get(f"/entities/{subject}/history")
    assert res.status_code == 200
    body = res.json()
    assert body["subject"] == subject
    assert "revenue" in body["measures"]


# --------------------------------------------------------------------------
# Full timeline
# --------------------------------------------------------------------------

def test_valid_subject_and_measure_returns_timeline(client, multi_period_subject_measure):
    subject, measure = multi_period_subject_measure
    res = client.get(f"/entities/{subject}/history", params={"measure": measure})
    assert res.status_code == 200
    body = res.json()
    assert body["subject"] == subject
    assert body["measure"] == measure
    assert body["total_facts"] >= 3
    assert body["metadata"]["interpolated"] is False
    assert body["metadata"]["is_source_of_truth"] is False

    total_points = sum(len(s["points"]) for s in body["series"]) + len(body["ambiguous_period_points"])
    assert total_points == body["total_facts"]


def test_known_subject_unknown_measure_returns_empty_not_404(client, multi_period_subject_measure):
    subject, _measure = multi_period_subject_measure
    res = client.get(f"/entities/{subject}/history", params={"measure": "a_measure_that_does_not_exist"})
    assert res.status_code == 200
    body = res.json()
    assert body["total_facts"] == 0
    assert body["series"] == []


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

def test_unknown_subject_404(client):
    res = client.get("/entities/definitely_not_a_real_subject_xyz/history")
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Points reference real, resolvable facts
# --------------------------------------------------------------------------

def test_history_points_reference_real_facts(client, store, multi_period_subject_measure):
    subject, measure = multi_period_subject_measure
    res = client.get(f"/entities/{subject}/history", params={"measure": measure})
    body = res.json()
    for series in body["series"]:
        for point in series["points"]:
            assert point["fact_id"] in store.facts
            fact = store.facts[point["fact_id"]]
            assert fact.subject == subject
            assert fact.measure == measure
