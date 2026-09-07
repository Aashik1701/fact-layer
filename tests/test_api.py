"""
Tests for api.py (milestone 5, STEP 1).

These run against the REAL persisted store (data/store.json, produced by a
full 6-document ingest) — api.py's whole point is to serve exactly what the
pipeline already computed, so a fixture rebuilding a fake store would test
nothing meaningful. LLM_MODE=replay + a network guard, same pattern as
test_store.py, because /ingest's idempotency test exercises Store.ingest()'s
code path (even though the short-circuit means no LLM call actually fires
for an already-ingested document).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/committed cache only")


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    yield


@pytest.fixture(scope="module")
def client():
    import api  # imported here, not at module top, so env vars above are set first
    assert len(api.STORE.facts) > 0, "data/store.json must hold a real ingested corpus for these tests"
    return TestClient(api.app)


@pytest.fixture(scope="module")
def store(client):
    import api
    return api.STORE


# --------------------------------------------------------------------------
# every endpoint 200 on the loaded corpus
# --------------------------------------------------------------------------

def test_documents_200(client, store):
    r = client.get("/documents")
    assert r.status_code == 200
    body = r.json()
    # The shipped demo store intentionally holds 5 of 6 documents — the 6th
    # (IMF) is the live-upload demo — so this asserts internal consistency,
    # not a specific count.
    assert body["total"] == len(store.ingested_docs) >= 1
    for doc in body["documents"]:
        assert doc["fact_count"] > 0
        assert doc["n_pages"] is not None


def test_facts_200(client):
    r = client.get("/facts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    assert len(body["facts"]) <= body["limit"]


def test_facts_pagination_and_filters(client, store):
    r = client.get("/facts", params={"limit": 5, "offset": 0})
    assert r.status_code == 200
    assert len(r.json()["facts"]) == 5

    any_fact = next(iter(store.facts.values()))
    r = client.get("/facts", params={"subject": any_fact.subject})
    assert r.status_code == 200
    assert all(f["subject"] == any_fact.subject for f in r.json()["facts"])

    r = client.get("/facts", params={"min_confidence": 0.99})
    assert r.status_code == 200
    assert all(f["confidence"] >= 0.99 for f in r.json()["facts"])


def test_clusters_200(client):
    r = client.get("/clusters", params={"min_size": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    for c in body["clusters"]:
        assert c["size"] >= 2
        assert len(c["facts"]) == c["size"]


def test_relations_200_and_confidence_sorted(client):
    r = client.get("/relations")
    assert r.status_code == 200
    rels = r.json()["relations"]
    assert len(rels) > 0
    confidences = [rel["confidence"] for rel in rels]
    assert confidences == sorted(confidences, reverse=True), \
        "GET /relations must default-sort by confidence descending"


def test_relations_filter_by_type_still_sorted(client):
    r = client.get("/relations", params={"type": "contradicts"})
    assert r.status_code == 200
    rels = r.json()["relations"]
    assert len(rels) > 0
    assert all(rel["relation"] == "contradicts" for rel in rels)
    confidences = [rel["confidence"] for rel in rels]
    assert confidences == sorted(confidences, reverse=True)


def test_relations_filter_by_fact_id(client):
    """The additive fact_id filter backs the frontend's 'Compare this fact'
    action — every relation returned must actually involve the requested
    fact, on either side, and every relation known to involve that fact must
    be included (no false negatives)."""
    all_rels = client.get("/relations").json()["relations"]
    target_fact_id = all_rels[0]["source_fact_id"]

    r = client.get("/relations", params={"fact_id": target_fact_id})
    assert r.status_code == 200
    rels = r.json()["relations"]
    assert len(rels) > 0
    assert all(
        rel["source_fact_id"] == target_fact_id or rel["target_fact_id"] == target_fact_id
        for rel in rels
    )
    expected_ids = {
        rel["relation_id"] for rel in all_rels
        if rel["source_fact_id"] == target_fact_id or rel["target_fact_id"] == target_fact_id
    }
    assert {rel["relation_id"] for rel in rels} == expected_ids


def test_relations_filter_by_fact_id_unknown_returns_empty(client):
    r = client.get("/relations", params={"fact_id": "f_does_not_exist"})
    assert r.status_code == 200
    assert r.json() == {"total": 0, "relations": []}


def test_stats_200_has_required_keys(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    for key in ("documents", "facts", "span_verification", "relations", "canonical", "clusters", "coverage"):
        assert key in body
    assert body["documents"]["count"] >= 1
    assert body["span_verification"]["verified"] > 0
    assert 0.0 <= body["span_verification"]["pass_rate"] <= 1.0
    cov = body["coverage"]
    assert 0 <= cov["facts_in_any_relation"] <= body["facts"]["total"]
    assert 0.0 <= cov["facts_in_any_relation_pct"] <= 100.0


# --------------------------------------------------------------------------
# /facts/{id} and /relations/{id}
# --------------------------------------------------------------------------

def test_fact_by_id_returns_full_evidence_for_merged_fact(client, store):
    """Whether a merged-evidence fact happens to exist is a property of
    WHICH documents are currently in the store — the shipped demo store
    intentionally holds only 5 of 6 documents (the 6th is the live-upload
    demo) and may have zero such facts, as it currently does. The API
    contract this test guards — /facts/{id} surfaces every evidence span
    via Store.get_evidence(), not just fact.evidence — is independent of
    that, so it's verified directly against a small fact injected into the
    live store, then removed."""
    from fact_layer.models import Evidence, Fact, Qualifiers, ValueKind
    from fact_layer.normalize import parse_quantity

    fact = Fact(
        subject="test_merge_subject", measure="test_merge_measure",
        value_kind=ValueKind.QUANTITY, value=parse_quantity("100000000"),
        qualifiers=Qualifiers(),
        evidence=Evidence(doc_id="d1", page=1, char_start=0, char_end=3,
                           verbatim_quote="100", verified=True),
        subject_raw="test", measure_raw="test",
    )
    extra = Evidence(doc_id="d1", page=5, char_start=10, char_end=13,
                      verbatim_quote="100", verified=True)

    store.facts[fact.fact_id] = fact
    store.extra_evidence[fact.fact_id] = [extra]
    try:
        r = client.get(f"/facts/{fact.fact_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["fact_id"] == fact.fact_id
        assert len(body["evidence"]) == 2, "merged fact must expose every evidence span, not just the primary one"
    finally:
        del store.facts[fact.fact_id]
        del store.extra_evidence[fact.fact_id]


def test_fact_by_id_404_on_unknown(client):
    r = client.get("/facts/does_not_exist")
    assert r.status_code == 404


def test_relation_by_id_returns_both_facts_and_gate(client):
    r = client.get("/relations")
    relation_id = r.json()["relations"][0]["relation_id"]

    r = client.get(f"/relations/{relation_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["source_fact"] is not None and body["target_fact"] is not None
    assert len(body["source_fact"]["evidence"]) >= 1
    assert "gate" in body
    assert "verdict" in body["gate"]
    assert "qualifier_diff" in body


def test_relation_by_id_404_on_unknown(client):
    r = client.get("/relations/deadbeef0000")
    assert r.status_code == 404


# --------------------------------------------------------------------------
# /page-image
# --------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_page_image_returns_valid_png(client, store):
    doc_id = next(iter(store.ingested_docs))
    r = client.get(f"/page-image/{doc_id}/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(_PNG_MAGIC)


def test_page_image_cached_on_second_request(client, store):
    doc_id = next(iter(store.ingested_docs))
    r1 = client.get(f"/page-image/{doc_id}/2")
    r2 = client.get(f"/page-image/{doc_id}/2")
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content


def test_page_image_404_on_unknown_doc(client):
    r = client.get("/page-image/not_a_real_doc_id/1")
    assert r.status_code == 404


def test_page_image_404_on_page_out_of_range(client, store):
    doc_id = next(iter(store.ingested_docs))
    r = client.get(f"/page-image/{doc_id}/99999")
    assert r.status_code == 404


# --------------------------------------------------------------------------
# /ingest idempotency
# --------------------------------------------------------------------------

def test_ingest_already_ingested_doc_is_idempotent_not_double_counted(client, store):
    path = os.path.join(ROOT, "starter-datasets", "delhivery",
                         "01-delhivery-prospectus-2022-excerpt.pdf")
    facts_before = len(store.facts)
    relations_before = len(store.relations)

    with open(path, "rb") as fh:
        r = client.post("/ingest", files={"file": ("01-delhivery-prospectus-2022-excerpt.pdf", fh, "application/pdf")})

    assert r.status_code == 200
    body = r.json()
    assert body["already_ingested"] is True
    assert body["new_facts"] == []
    assert body["new_relations"] == []
    assert body["llm_calls_made"] == 0
    assert len(store.facts) == facts_before
    assert len(store.relations) == relations_before


def test_ingest_rejects_non_pdf(client):
    r = client.post("/ingest", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
