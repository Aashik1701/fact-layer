"""Tests for the retrieval diagnostic endpoints added to api.py:
GET /retrieval/stats and GET /facts/{fact_id}/candidates.

Runs against the real persisted store (data/store.json), same convention
as test_api.py — these are read-only diagnostic endpoints, not gated
behind RETRIEVAL_ENABLED (see api.py's endpoint docstring for why).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_CACHE_DIR = os.path.join(ROOT, "cache", "embeddings", "models")


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/cached model only")


@pytest.fixture(scope="module")
def _shared_index_dir(tmp_path_factory):
    # One shared on-disk index dir for the whole module: /facts/{id}/
    # candidates upserts the full 554-fact corpus on every call, and the
    # persistent embedding cache (keyed by text+model+dimension, not by
    # test) means only the FIRST test in this module pays real embedding
    # cost — the rest hit the cache. A fresh dir per test would recompute
    # 554 embeddings five times over for no correctness benefit.
    return str(tmp_path_factory.mktemp("retrieval_index"))


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch, _shared_index_dir):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)

    monkeypatch.setenv("EMBEDDING_MODE", "replay")
    monkeypatch.setenv("EMBEDDING_MODEL_CACHE_DIR", _MODEL_CACHE_DIR)
    monkeypatch.setenv("RETRIEVAL_INDEX_PATH", _shared_index_dir)

    import requests.adapters
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _no_network)

    from fact_layer.retrieval import reset_index_cache
    reset_index_cache()
    yield
    reset_index_cache()


@pytest.fixture(scope="module")
def client():
    import api  # imported here, not at module top, so env vars above are set first
    assert len(api.STORE.facts) > 0, "data/store.json must hold a real ingested corpus for these tests"
    return TestClient(api.app)


@pytest.fixture()
def any_fact_id(client):
    import api
    return next(iter(api.STORE.facts))


def test_retrieval_stats_200(client):
    r = client.get("/retrieval/stats")
    assert r.status_code == 200
    body = r.json()
    for key in ("indexed_facts", "embedding_model", "embedding_dimension",
                "lexical_index_size", "vector_index_size", "embedding_cache_size",
                "configured_top_k", "configured_lexical_weight", "configured_semantic_weight",
                "retrieval_enabled"):
        assert key in body


def test_candidates_200_for_real_fact(client, any_fact_id):
    r = client.get(f"/facts/{any_fact_id}/candidates")
    assert r.status_code == 200
    body = r.json()
    assert body["fact_id"] == any_fact_id
    assert isinstance(body["candidates"], list)
    for key in ("lexical_count", "semantic_count", "after_block_count", "final_top_k_count"):
        assert key in body["funnel"]
    for row in body["candidates"]:
        for key in ("fact_id", "lexical_score", "semantic_score", "hybrid_score",
                    "lexical_rank", "semantic_rank", "blocking_status", "blocking_reason"):
            assert key in row
        assert row["blocking_status"] in ("candidate", "blocked")


def test_candidates_respects_top_k_query_param(client, any_fact_id):
    r = client.get(f"/facts/{any_fact_id}/candidates", params={"top_k": 3})
    assert r.status_code == 200
    assert r.json()["returned"] <= 3


def test_candidates_404_for_unknown_fact(client):
    r = client.get("/facts/nonexistent_fact_id/candidates")
    assert r.status_code == 404


def test_candidates_never_include_the_query_fact_itself(client, any_fact_id):
    r = client.get(f"/facts/{any_fact_id}/candidates")
    ids = [c["fact_id"] for c in r.json()["candidates"]]
    assert any_fact_id not in ids
