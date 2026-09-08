"""Tests for fact_layer/retrieval/embeddings.py.

Uses the real `BAAI/bge-small-en-v1.5` model already downloaded into this
repo's `cache/embeddings/models/` (a prerequisite documented in the
README/plan — a fresh clone needs one `EMBEDDING_MODE=live` run first, the
same one-time-network contract `llm.py` has for its own cache). Every test
here runs with EMBEDDING_MODE=replay and a patched HTTP layer, proving the
network is never touched once the model is cached (section 17).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.retrieval.embeddings import (
    EmbeddingCache,
    EmbeddingUnavailableError,
    FastEmbedProvider,
    embed_texts_cached,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "embeddings", "models")
_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted in EMBEDDING_MODE=replay")


@pytest.fixture(autouse=True)
def _no_http(monkeypatch):
    import requests.adapters
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _no_network)
    yield


def _provider() -> FastEmbedProvider:
    return FastEmbedProvider(model_name=_MODEL_NAME, cache_dir=_MODEL_CACHE_DIR, mode="replay")


# --------------------------------------------------------------------------
# Provider — deterministic, network-free once cached
# --------------------------------------------------------------------------

def test_replay_mode_uses_cached_model_without_network():
    provider = _provider()
    vec = provider.embed("subject: delhivery measure: revenue")
    assert vec.shape == (provider.dimension(),)


def test_embed_is_deterministic():
    provider = _provider()
    v1 = provider.embed("subject: delhivery measure: revenue")
    v2 = provider.embed("subject: delhivery measure: revenue")
    assert np.array_equal(v1, v2)


def test_embed_many_matches_embed_one_by_one():
    provider = _provider()
    texts = ["subject: a", "subject: b"]
    batch = provider.embed_many(texts)
    singles = np.stack([provider.embed(t) for t in texts])
    assert np.allclose(batch, singles)


def test_replay_mode_fails_explicitly_when_model_not_cached(tmp_path):
    provider = FastEmbedProvider(model_name=_MODEL_NAME, cache_dir=str(tmp_path / "empty_cache"), mode="replay")
    with pytest.raises(EmbeddingUnavailableError):
        provider.embed("anything")


def test_model_id_and_dimension_are_reported():
    provider = _provider()
    assert provider.model_id() == _MODEL_NAME
    assert provider.dimension() > 0


# --------------------------------------------------------------------------
# EmbeddingCache — keyed by text+model+dimension, never fact_id alone
# --------------------------------------------------------------------------

def test_cache_miss_then_hit(tmp_path):
    cache = EmbeddingCache(str(tmp_path / "cache.sqlite3"))
    assert cache.get_many(["hello"], "model-a", 4) == {}
    cache.put_many({"hello": np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)}, "model-a", 4)
    hits = cache.get_many(["hello"], "model-a", 4)
    assert np.allclose(hits["hello"], [1.0, 2.0, 3.0, 4.0])


def test_cache_invalidates_on_model_change(tmp_path):
    cache = EmbeddingCache(str(tmp_path / "cache.sqlite3"))
    cache.put_many({"hello": np.array([1.0, 2.0], dtype=np.float32)}, "model-a", 2)
    assert cache.get_many(["hello"], "model-b", 2) == {}


def test_cache_invalidates_on_dimension_change(tmp_path):
    cache = EmbeddingCache(str(tmp_path / "cache.sqlite3"))
    cache.put_many({"hello": np.array([1.0, 2.0], dtype=np.float32)}, "model-a", 2)
    assert cache.get_many(["hello"], "model-a", 3) == {}


def test_cache_persists_across_reopen(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    cache1 = EmbeddingCache(path)
    cache1.put_many({"hello": np.array([1.0], dtype=np.float32)}, "m", 1)
    cache1.close()

    cache2 = EmbeddingCache(path)
    assert np.allclose(cache2.get_many(["hello"], "m", 1)["hello"], [1.0])


# --------------------------------------------------------------------------
# embed_texts_cached — incremental: only missing texts are (re)computed
# --------------------------------------------------------------------------

def test_embed_texts_cached_only_computes_missing(tmp_path, monkeypatch):
    provider = _provider()
    cache = EmbeddingCache(str(tmp_path / "cache.sqlite3"))

    calls = []
    real_embed_many = provider.embed_many

    def _tracking_embed_many(texts):
        calls.append(list(texts))
        return real_embed_many(texts)

    monkeypatch.setattr(provider, "embed_many", _tracking_embed_many)

    first = embed_texts_cached(["a", "b"], provider, cache)
    assert calls == [["a", "b"]]

    second = embed_texts_cached(["a", "b", "c"], provider, cache)
    assert calls[-1] == ["c"]   # only the new text is recomputed
    assert np.array_equal(first["a"], second["a"])
    assert np.array_equal(first["b"], second["b"])


def test_embed_texts_cached_dedupes_repeated_texts_in_one_call(tmp_path, monkeypatch):
    provider = _provider()
    cache = EmbeddingCache(str(tmp_path / "cache.sqlite3"))
    calls = []
    monkeypatch.setattr(provider, "embed_many", lambda texts: (calls.append(list(texts)), provider.__class__.embed_many(provider, texts))[1])
    result = embed_texts_cached(["dup", "dup", "dup"], provider, cache)
    assert calls == [["dup"]]
    assert "dup" in result
