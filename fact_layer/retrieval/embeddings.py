"""
Embedding generation, isolated behind one interface (`EmbeddingProvider`)
so the rest of the codebase never imports `fastembed` directly — same
shape as `fact_layer.storage.FactStore`/`JsonFactStore`: one ABC, one real
local implementation, model identity/version threaded through explicitly
rather than assumed.

Determinism & replay-safety (section 17 — this project's zero-network
replay guarantee must not be weakened):
  - ONNX inference on CPU is deterministic for a fixed model file and
    input — no dropout, no sampling, verified in this environment
    (identical input -> bit-identical output vector across calls).
  - `EMBEDDING_MODE=live` (default): download the model into
    `embedding_model_cache_dir` on first use if it isn't already cached.
  - `EMBEDDING_MODE=replay`: forces `HF_HUB_OFFLINE=1` before touching
    fastembed and never attempts a download. A cache miss raises
    `EmbeddingUnavailableError` immediately — never a fake/random vector
    (section 17's explicit requirement) — mirroring `llm.py`'s
    `ReplayCacheMiss` behavior for the exact same reason: tests and
    graders must get a clear, honest failure, not silently-wrong data.

`EmbeddingCache` is a persistent, incremental cache keyed by
`sha256(text + model_id + dimension)` (never fact_id alone — see this
module's docstring on `_cache_key` for why), stored as a `float32` BLOB in
sqlite3. It lives under `data/retrieval_index/` (gitignored, rebuildable —
see fact_layer/retrieval/index.py), not `cache/llm/`'s pattern of being
committed: unlike an LLM call, recomputing an embedding costs no money and
needs no credentials, only local CPU time, so there is nothing here that a
fresh clone cannot regenerate for itself.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np


class EmbeddingUnavailableError(RuntimeError):
    """Raised when EMBEDDING_MODE=replay (or any other reason) means an
    embedding cannot be produced. Never caught to substitute a fake
    vector — every caller either handles this explicitly or lets it
    propagate."""


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """A single dense vector, shape (dimension(),), dtype float32."""

    @abstractmethod
    def embed_many(self, texts: list[str]) -> np.ndarray:
        """Shape (len(texts), dimension()), dtype float32. Batches where
        the underlying model supports it — callers should prefer this over
        looping `embed()` for more than a couple of texts."""

    @abstractmethod
    def model_id(self) -> str:
        """Stable identifier for the exact model+version in use — part of
        the embedding cache key and persisted vector-store metadata, so a
        model change is detectable rather than silently mixing vector
        spaces."""

    @abstractmethod
    def dimension(self) -> int:
        ...


class FastEmbedProvider(EmbeddingProvider):
    """Local ONNX embedding via the `fastembed` package (no torch). See
    this module's docstring for the live/replay network-access contract."""

    def __init__(self, model_name: str, cache_dir: str, mode: str = "live") -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._mode = mode
        self._model = None      # lazy: constructing a FastEmbedProvider must not require network
        self._dimension: Optional[int] = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        os.makedirs(self._cache_dir, exist_ok=True)
        if self._mode == "replay":
            # Force offline BEFORE importing/instantiating — matches
            # llm.py's "replay mode dials out to nothing" guarantee.
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            from fastembed import TextEmbedding
        except ImportError as e:  # pragma: no cover - dependency always installed in this repo
            raise EmbeddingUnavailableError(
                f"fastembed is not installed: {e}. Add it to requirements.txt and `pip install -r requirements.txt`."
            ) from e

        try:
            self._model = TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir)
        except Exception as e:
            if self._mode == "replay":
                raise EmbeddingUnavailableError(
                    f"EMBEDDING_MODE=replay and model {self._model_name!r} is not cached at "
                    f"{self._cache_dir!r} — no network call was attempted. Run once with "
                    f"EMBEDDING_MODE=live to populate the cache, then switch back to replay."
                ) from e
            raise EmbeddingUnavailableError(f"failed to load embedding model {self._model_name!r}: {e}") from e

        # dimension() must work without embedding anything — probe once at load time.
        probe = list(self._model.embed(["_dimension_probe_"]))[0]
        self._dimension = int(probe.shape[0])

    def embed(self, text: str) -> np.ndarray:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension()), dtype=np.float32)
        self._ensure_loaded()
        vectors = list(self._model.embed(list(texts)))
        return np.asarray(vectors, dtype=np.float32)

    def model_id(self) -> str:
        return self._model_name

    def dimension(self) -> int:
        self._ensure_loaded()
        assert self._dimension is not None
        return self._dimension


def _cache_key(text: str, model_id: str, dimension: int) -> str:
    """Hashes text + model_id + dimension together — NEVER fact_id alone
    (section 6's explicit requirement). If `fact_to_retrieval_text()`'s
    output for a fact changes (a qualifier gets re-resolved, say), the key
    changes and the stale vector is simply never looked up again — no
    explicit invalidation step needed, the same "wrong key never matches"
    property `llm.py`'s prompt-hash cache already relies on."""
    seed = f"{model_id}\x00{dimension}\x00{text}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Persistent, incremental embedding cache. One sqlite3 file, one
    table, `key` (see `_cache_key`) as primary key so a repeat call is a
    single indexed lookup."""

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # check_same_thread=False: see LexicalIndex's identical comment —
        # this cache lives inside the same process-wide RetrievalIndex
        # singleton, accessed from multiple threads, serialized by
        # RetrievalIndex._lock rather than by sqlite3 thread affinity.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            " key TEXT PRIMARY KEY,"
            " model_id TEXT NOT NULL,"
            " dimension INTEGER NOT NULL,"
            " vector BLOB NOT NULL"
            ")"
        )
        self._conn.commit()

    def get_many(self, texts: list[str], model_id: str, dimension: int) -> dict[str, np.ndarray]:
        """text -> vector, only for texts already cached (a cache miss is
        simply absent from the returned dict, never an exception)."""
        if not texts:
            return {}
        keys = {t: _cache_key(t, model_id, dimension) for t in texts}
        placeholders = ",".join("?" for _ in keys)
        rows = self._conn.execute(
            f"SELECT key, vector FROM embeddings WHERE key IN ({placeholders})",
            list(keys.values()),
        ).fetchall()
        by_key = {k: np.frombuffer(v, dtype=np.float32) for k, v in rows}
        return {t: by_key[k] for t, k in keys.items() if k in by_key}

    def put_many(self, items: dict[str, np.ndarray], model_id: str, dimension: int) -> None:
        """text -> vector, for newly computed embeddings only (callers
        should already have filtered out cache hits via get_many)."""
        if not items:
            return
        rows = [
            (_cache_key(text, model_id, dimension), model_id, dimension, np.asarray(vec, dtype=np.float32).tobytes())
            for text, vec in items.items()
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO embeddings (key, model_id, dimension, vector) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def size(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


def embed_texts_cached(
    texts: list[str], provider: EmbeddingProvider, cache: EmbeddingCache
) -> dict[str, np.ndarray]:
    """The one function that ties a provider + cache together: compute
    only what's missing, persist it, return text -> vector for everything
    requested. This is what makes incremental ingestion (section 6) cheap
    — a new document's facts only pay embedding cost for retrieval texts
    the cache hasn't seen before."""
    model_id, dimension = provider.model_id(), provider.dimension()
    unique_texts = list(dict.fromkeys(texts))   # de-dup, preserve order
    hits = cache.get_many(unique_texts, model_id, dimension)
    missing = [t for t in unique_texts if t not in hits]
    if missing:
        vectors = provider.embed_many(missing)
        new_items = {t: vectors[i] for i, t in enumerate(missing)}
        cache.put_many(new_items, model_id, dimension)
        hits.update(new_items)
    return hits


__all__ = [
    "EmbeddingProvider",
    "FastEmbedProvider",
    "EmbeddingCache",
    "EmbeddingUnavailableError",
    "embed_texts_cached",
]
