"""
GET /graph/{node_type}/{node_id} and GET /graph/search — API tests.

Covers the contract the frontend depends on, and the two properties that
must hold server-side rather than by client convention: the depth limit is
enforced by the server, and errors are sanitized.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api as api_module
from fact_layer import graph as g


@pytest.fixture(scope="module")
def client():
    return TestClient(api_module.app)


@pytest.fixture(scope="module")
def store():
    return api_module.STORE


@pytest.fixture(scope="module")
def anchors(store):
    rel = store.relations[0]
    fact_id = rel.source_fact_id
    return {
        "fact_id": fact_id,
        "subject": store.facts[fact_id].subject,
        "doc_id": next(iter(store.ingested_docs)),
    }


# --------------------------------------------------------------------------
# Happy paths for every root type
# --------------------------------------------------------------------------

def test_fact_root(client, anchors):
    res = client.get(f"/graph/fact/{anchors['fact_id']}?depth=2")
    assert res.status_code == 200
    body = res.json()
    assert body["root"]["type"] == "fact"
    assert body["root"]["source_id"] == anchors["fact_id"]
    assert body["metadata"]["node_count"] >= 1
    for section in ("root", "nodes", "edges", "metadata"):
        assert section in body


def test_entity_root(client, anchors):
    res = client.get(f"/graph/entity/{anchors['subject']}?depth=1")
    assert res.status_code == 200
    assert res.json()["root"]["type"] == "entity"


def test_document_root(client, anchors):
    res = client.get(f"/graph/document/{anchors['doc_id']}?depth=1")
    assert res.status_code == 200
    assert res.json()["root"]["type"] == "document"


def test_evidence_root_uses_index_query_param(client, anchors):
    res = client.get(f"/graph/evidence/{anchors['fact_id']}?index=0&depth=1")
    assert res.status_code == 200
    body = res.json()
    assert body["root"]["type"] == "evidence"
    assert any(n["type"] == "document" for n in body["nodes"])


def test_depth_zero_returns_only_root(client, anchors):
    body = client.get(f"/graph/fact/{anchors['fact_id']}?depth=0").json()
    assert body["metadata"]["node_count"] == 1
    assert body["edges"] == []


def test_response_is_deterministic(client, anchors):
    url = f"/graph/fact/{anchors['fact_id']}?depth=2"
    first = client.get(url).json()
    for _ in range(3):
        assert client.get(url).json() == first


def test_metadata_declares_the_projection_contract(client, anchors):
    md = client.get(f"/graph/fact/{anchors['fact_id']}?depth=1").json()["metadata"]
    assert md["is_source_of_truth"] is False
    assert "Store" in md["projection_of"]
    assert md["max_depth"] == g.MAX_DEPTH


# --------------------------------------------------------------------------
# Bounds are enforced by the SERVER, not trusted from the client
# --------------------------------------------------------------------------

def test_excessive_depth_is_rejected_by_validation(client, anchors):
    res = client.get(f"/graph/fact/{anchors['fact_id']}?depth=99")
    assert res.status_code == 422


def test_negative_depth_is_rejected(client, anchors):
    assert client.get(f"/graph/fact/{anchors['fact_id']}?depth=-1").status_code == 422


def test_excessive_max_nodes_is_rejected(client, anchors):
    assert client.get(f"/graph/fact/{anchors['fact_id']}?max_nodes=100000").status_code == 422


def test_node_cap_is_honoured_and_reported(client, anchors):
    body = client.get(f"/graph/fact/{anchors['fact_id']}?depth=4&max_nodes=15").json()
    assert body["metadata"]["node_count"] <= 15
    assert body["metadata"]["truncated"] is True
    assert body["metadata"]["truncation_reasons"]


def test_no_edge_references_a_missing_node(client, anchors):
    """A truncated graph must not emit dangling edges."""
    for cap in (5, 20, 80):
        body = client.get(f"/graph/fact/{anchors['fact_id']}?depth=4&max_nodes={cap}").json()
        ids = {n["id"] for n in body["nodes"]}
        for edge in body["edges"]:
            assert edge["source"] in ids
            assert edge["target"] in ids


# --------------------------------------------------------------------------
# Errors (spec §45, §48)
# --------------------------------------------------------------------------

def test_unknown_fact_returns_404(client):
    res = client.get("/graph/fact/does_not_exist")
    assert res.status_code == 404
    assert "unknown" in res.json()["detail"]


def test_unknown_entity_returns_404(client):
    assert client.get("/graph/entity/no_such_entity").status_code == 404


def test_invalid_node_type_returns_400(client):
    res = client.get("/graph/bogus/xyz")
    assert res.status_code == 400
    assert "node_type" in res.json()["detail"]


def test_evidence_index_out_of_range_returns_404(client, anchors):
    assert client.get(f"/graph/evidence/{anchors['fact_id']}?index=9999").status_code == 404


def test_path_traversal_identifier_is_just_an_unknown_id(client):
    res = client.get("/graph/entity/..%2F..%2Fetc%2Fpasswd")
    assert res.status_code == 404
    assert "root:" not in res.text


def test_errors_do_not_leak_internals(client):
    res = client.get("/graph/fact/nope")
    assert "Traceback" not in res.text
    assert "/Users/" not in res.text
    assert "site-packages" not in res.text


def test_no_filesystem_paths_in_a_successful_response(client, anchors):
    """Document nodes carry a filename, never an on-disk path."""
    body = client.get(f"/graph/fact/{anchors['fact_id']}?depth=2").text
    assert "/Users/" not in body
    assert "data/uploads" not in body


# --------------------------------------------------------------------------
# Relation edges are authoritative
# --------------------------------------------------------------------------

def test_relation_edges_exist_in_the_store(client, store, anchors):
    authoritative = {
        (r.source_fact_id, r.target_fact_id, r.relation.value.upper())
        for r in store.relations
    }
    body = client.get(f"/graph/fact/{anchors['fact_id']}?depth=2").json()
    seen = 0
    for edge in body["edges"]:
        if edge["type"] in g.STRUCTURAL_EDGE_TYPES:
            continue
        triple = (edge["metadata"]["source_fact_id"],
                  edge["metadata"]["target_fact_id"], edge["type"])
        assert triple in authoritative
        assert edge["metadata"]["confidence"] is not None
        assert edge["metadata"]["reason_code"]
        seen += 1
    assert seen > 0, "the anchor fact should have at least one relation edge"


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def test_search_returns_results(client, anchors):
    res = client.get(f"/graph/search?q={anchors['subject'][:6]}")
    assert res.status_code == 200
    body = res.json()
    assert "results" in body
    for row in body["results"]:
        assert row["type"] in ("entity", "fact", "document")


def test_search_requires_a_query(client):
    assert client.get("/graph/search?q=").status_code == 422


def test_search_limit_is_bounded(client):
    assert client.get("/graph/search?q=a&limit=9999").status_code == 422
    body = client.get("/graph/search?q=a&limit=5").json()
    assert len(body["results"]) <= 5
