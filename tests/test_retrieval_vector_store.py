"""Tests for fact_layer/retrieval/vector_store.py (LocalNumpyVectorStore).
No embedding model involved — vectors are hand-built numpy arrays, so
these run fast and network-free unconditionally.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.retrieval.vector_store import LocalNumpyVectorStore


def _mkstore(tmp_path, dimension=4):
    return LocalNumpyVectorStore(
        str(tmp_path / "vectors.npz"), str(tmp_path / "vector_meta.json"), dimension=dimension,
    )


def test_insert_and_search_returns_most_similar_first(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {"tag": "a"})
    store.upsert("b", np.array([0.9, 0.1, 0.0, 0.0]), {"tag": "b"})
    store.upsert("c", np.array([0.0, 0.0, 1.0, 0.0]), {"tag": "c"})

    results = store.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=3)
    ids = [r.fact_id for r in results]
    assert ids[0] == "a"
    assert ids[1] == "b"     # closer to the query than c
    assert ids[2] == "c"
    assert results[0].score > results[1].score > results[2].score


def test_metadata_filtering(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {"kind": "quantity"})
    store.upsert("b", np.array([0.99, 0.01, 0.0, 0.0]), {"kind": "text"})

    results = store.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=5,
                            metadata_filter=lambda m: m["kind"] == "quantity")
    assert [r.fact_id for r in results] == ["a"]


def test_upsert_existing_id_overwrites(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {"v": 1})
    store.upsert("a", np.array([0.0, 1.0, 0.0, 0.0]), {"v": 2})
    assert len(store) == 1
    results = store.search(np.array([0.0, 1.0, 0.0, 0.0]), top_k=1)
    assert results[0].metadata["v"] == 2


def test_delete_excludes_from_search(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {})
    store.upsert("b", np.array([0.0, 1.0, 0.0, 0.0]), {})
    store.delete("a")
    assert len(store) == 1
    results = store.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=5)
    assert [r.fact_id for r in results] == ["b"]


def test_persistence_round_trip(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {"tag": "a"})
    store.upsert("b", np.array([0.0, 1.0, 0.0, 0.0]), {"tag": "b"})
    store.save()

    reloaded = _mkstore(tmp_path)   # constructor calls load() internally
    assert len(reloaded) == 2
    results = reloaded.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=1)
    assert results[0].fact_id == "a"
    assert results[0].metadata["tag"] == "a"


def test_save_compacts_tombstoned_rows(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {})
    store.upsert("b", np.array([0.0, 1.0, 0.0, 0.0]), {})
    store.delete("a")
    store.save()

    reloaded = _mkstore(tmp_path)
    assert len(reloaded) == 1
    assert reloaded.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=5)[0].fact_id == "b"


def test_rebuild_empties_store(tmp_path):
    store = _mkstore(tmp_path)
    store.upsert("a", np.array([1.0, 0.0, 0.0, 0.0]), {})
    store.rebuild()
    assert len(store) == 0
    assert store.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=5) == []


def test_search_on_empty_store_returns_empty_list(tmp_path):
    store = _mkstore(tmp_path)
    assert store.search(np.array([1.0, 0.0, 0.0, 0.0]), top_k=5) == []
