"""
GET /facts/{fact_id}/lineage and GET /relations/{relation_id}/lineage — API tests.
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
def anchors(store):
    rel = store.relations[0]
    return {
        "relation_id": next(iter(api_module._relation_index())),
        "fact_id": rel.source_fact_id,
    }


# --------------------------------------------------------------------------
# Fact lineage
# --------------------------------------------------------------------------

def test_fact_lineage_valid_id(client, anchors, store):
    res = client.get(f"/facts/{anchors['fact_id']}/lineage")
    assert res.status_code == 200
    body = res.json()
    assert body["root_conclusion"]["kind"] == "fact"
    assert body["root_conclusion"]["fact_id"] == anchors["fact_id"]
    assert any(n["source_id"] == anchors["fact_id"] for n in body["facts"])


def test_fact_lineage_unknown_id_404(client):
    res = client.get("/facts/not_a_real_fact_id/lineage")
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Relation lineage
# --------------------------------------------------------------------------

def test_relation_lineage_valid_id(client, anchors, store):
    rid = anchors["relation_id"]
    res = client.get(f"/relations/{rid}/lineage")
    assert res.status_code == 200
    body = res.json()
    assert body["root_conclusion"]["kind"] == "relation"
    assert body["root_conclusion"]["relation_id"] == rid

    rel = api_module._relation_index()[rid]
    fact_ids = {n["source_id"] for n in body["facts"]}
    assert rel.source_fact_id in fact_ids
    assert rel.target_fact_id in fact_ids


def test_relation_lineage_matches_stored_relation_fields(client, anchors):
    rid = anchors["relation_id"]
    rel = api_module._relation_index()[rid]
    body = client.get(f"/relations/{rid}/lineage").json()
    root = body["root_conclusion"]
    assert root["relation"] == rel.relation.value
    assert root["confidence"] == rel.confidence
    assert root["explanation"] == rel.explanation
    assert root["decided_by"] == rel.decided_by


def test_relation_lineage_unknown_id_404_never_synthesized(client, store):
    """§31: an unknown relation_id must 404, never be built from an
    arbitrary pair of real fact ids."""
    res = client.get("/relations/not_a_real_relation_id/lineage")
    assert res.status_code == 404


def test_relation_lineage_never_produced_for_two_arbitrary_fact_ids(client, store):
    """There is no endpoint shape that accepts two fact ids directly for
    lineage — only a resolved relation_id. Confirm the comparability path
    (two facts, no relation) is NOT reachable through this route at all."""
    fact_ids = list(store.facts.keys())[:2]
    res = client.get(f"/relations/{fact_ids[0]}-{fact_ids[1]}/lineage")
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Sanitized errors: no traceback, no filesystem path
# --------------------------------------------------------------------------

def test_error_bodies_contain_no_repo_path(client):
    res = client.get("/facts/nope/lineage")
    assert "/Users/" not in res.text
    assert "Traceback" not in res.text
