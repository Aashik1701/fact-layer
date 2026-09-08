"""
The vector store: an INDEX over embeddings, never the source of truth for
a fact (section 7's explicit requirement — `fact_layer.store.Store`/
`data/store.json` remain authoritative; a vector row that outlives its
fact, or is never reconciled against one, must never surface as if it
were live data — see `VectorStore.search()`'s metadata-filter contract
and `fact_layer/retrieval/index.py`'s reconciliation against `Store.facts`).

    VectorStore (ABC)
        |
        +-- LocalNumpyVectorStore  -- current: embedded, local, zero
                                       external services

Why a hand-rolled local store, not Chroma/FAISS/Qdrant
-------------------------------------------------------
Same reasoning `fact_layer.storage` already gives for JSON over Postgres:
right-size the infrastructure to the *measured* need, not the fashionable
default. At the scale this task itself specifies (the 100-document/
12,000-fact synthetic benchmark; this project's real corpus is 554 facts),
a brute-force cosine search over an in-memory `(N, 384)` float32 matrix is
a single dense matrix-vector product — sub-millisecond to low-single-digit
milliseconds even at N=12,000 — so an approximate-nearest-neighbour index
(what FAISS/Qdrant/Chroma exist to provide) buys nothing measurable here
and would add a binary dependency with its own platform-wheel and version
surface for no benefit. `VectorStore` is still a real interface — a future
`FaissVectorStore` or `QdrantVectorStore` could implement it without any
caller (`index.py`, `hybrid.py`, `api.py`) changing — exactly the seam
`storage.py` draws between `JsonFactStore` and its documented
`PostgresFactStore` skeleton.

Persistence: one `.npz` (the float32 matrix, L2-normalized at insert time
so search is a plain dot product) plus one `.json` manifest (row order ->
fact_id + the metadata section 7 asks for: cluster_id, canonical subject/
measure, issuer, value_kind, period label, scope, embedding model/version).
Deletion is rare (a fact is essentially never removed from FactStore in
this codebase's lifecycle) so it is handled by marking a row's fact_id
`None` (tombstone) and compacting on the next `save()`/`rebuild()`, rather
than paying for an in-place-shrink on every delete.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np


@dataclass
class VectorRecord:
    fact_id: str
    score: float
    metadata: dict


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, fact_id: str, vector: np.ndarray, metadata: dict) -> None:
        ...

    @abstractmethod
    def upsert_many(self, items: list[tuple[str, np.ndarray, dict]]) -> None:
        ...

    @abstractmethod
    def delete(self, fact_id: str) -> None:
        ...

    @abstractmethod
    def search(
        self, query_vector: np.ndarray, top_k: int,
        metadata_filter: Optional[Callable[[dict], bool]] = None,
    ) -> list[VectorRecord]:
        ...

    @abstractmethod
    def save(self) -> None:
        ...

    @abstractmethod
    def load(self) -> None:
        ...

    @abstractmethod
    def rebuild(self) -> None:
        """Drop everything and start empty — callers (index.py's
        rebuild_retrieval_index()) re-upsert from FactStore afterward."""

    @abstractmethod
    def __len__(self) -> int:
        ...


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


class LocalNumpyVectorStore(VectorStore):
    def __init__(self, matrix_path: str, meta_path: str, dimension: int) -> None:
        self.matrix_path = matrix_path
        self.meta_path = meta_path
        self.dimension = dimension
        self._ids: list[Optional[str]] = []          # None = tombstoned row
        self._metadata: list[dict] = []
        self._matrix: np.ndarray = np.zeros((0, dimension), dtype=np.float32)
        self._id_to_row: dict[str, int] = {}
        self.load()

    # ---- mutation -------------------------------------------------------

    def upsert(self, fact_id: str, vector: np.ndarray, metadata: dict) -> None:
        self.upsert_many([(fact_id, vector, metadata)])

    def upsert_many(self, items: list[tuple[str, np.ndarray, dict]]) -> None:
        if not items:
            return
        new_rows = []
        for fact_id, vector, metadata in items:
            v = _normalize(np.asarray(vector, dtype=np.float32).reshape(1, -1))[0]
            if fact_id in self._id_to_row:
                row = self._id_to_row[fact_id]
                self._matrix[row] = v
                self._metadata[row] = metadata
            else:
                new_rows.append((fact_id, v, metadata))

        if new_rows:
            add_matrix = np.stack([v for _, v, _ in new_rows], axis=0)
            start = len(self._ids)
            self._matrix = np.vstack([self._matrix, add_matrix]) if len(self._ids) else add_matrix
            for i, (fact_id, _, metadata) in enumerate(new_rows):
                self._ids.append(fact_id)
                self._metadata.append(metadata)
                self._id_to_row[fact_id] = start + i

    def delete(self, fact_id: str) -> None:
        row = self._id_to_row.pop(fact_id, None)
        if row is not None:
            self._ids[row] = None
            self._metadata[row] = {}

    # ---- query ------------------------------------------------------------

    def search(
        self, query_vector: np.ndarray, top_k: int,
        metadata_filter: Optional[Callable[[dict], bool]] = None,
    ) -> list[VectorRecord]:
        if len(self._ids) == 0:
            return []
        q = _normalize(np.asarray(query_vector, dtype=np.float32).reshape(1, -1))[0]
        scores = self._matrix @ q   # cosine similarity, both sides L2-normalized

        order = np.argsort(-scores)
        out: list[VectorRecord] = []
        for row in order:
            fact_id = self._ids[row]
            if fact_id is None:
                continue
            meta = self._metadata[row]
            if metadata_filter is not None and not metadata_filter(meta):
                continue
            out.append(VectorRecord(fact_id=fact_id, score=float(scores[row]), metadata=meta))
            if len(out) >= top_k:
                break
        return out

    # ---- persistence --------------------------------------------------

    def _compact(self) -> None:
        keep = [i for i, fid in enumerate(self._ids) if fid is not None]
        if len(keep) == len(self._ids):
            return
        self._matrix = self._matrix[keep] if keep else np.zeros((0, self.dimension), dtype=np.float32)
        self._ids = [self._ids[i] for i in keep]
        self._metadata = [self._metadata[i] for i in keep]
        self._id_to_row = {fid: i for i, fid in enumerate(self._ids)}

    def save(self) -> None:
        self._compact()
        os.makedirs(os.path.dirname(self.matrix_path), exist_ok=True)
        # np.savez_compressed appends ".npz" to its target unless given an
        # already-open file handle — write through one explicitly so the
        # temp path is exactly what we then os.replace() from.
        tmp_matrix = f"{self.matrix_path}.tmp"
        with open(tmp_matrix, "wb") as fh:
            np.savez_compressed(fh, matrix=self._matrix)
        os.replace(tmp_matrix, self.matrix_path)

        tmp_meta = f"{self.meta_path}.tmp"
        with open(tmp_meta, "w", encoding="utf-8") as fh:
            json.dump(
                {"dimension": self.dimension, "ids": self._ids, "metadata": self._metadata},
                fh,
            )
        os.replace(tmp_meta, self.meta_path)

    def load(self) -> None:
        if not (os.path.exists(self.matrix_path) and os.path.exists(self.meta_path)):
            self._ids, self._metadata = [], []
            self._matrix = np.zeros((0, self.dimension), dtype=np.float32)
            self._id_to_row = {}
            return
        with open(self.meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        loaded = np.load(self.matrix_path)
        self._matrix = loaded["matrix"].astype(np.float32)
        self._ids = meta["ids"]
        self._metadata = meta["metadata"]
        self._id_to_row = {fid: i for i, fid in enumerate(self._ids) if fid is not None}

    def rebuild(self) -> None:
        self._ids, self._metadata = [], []
        self._matrix = np.zeros((0, self.dimension), dtype=np.float32)
        self._id_to_row = {}

    def __len__(self) -> int:
        return len(self._id_to_row)


__all__ = ["VectorStore", "VectorRecord", "LocalNumpyVectorStore"]
