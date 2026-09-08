"""
RetrievalIndex: orchestrates the lexical index, vector store, embedding
provider and embedding cache behind one object, so `fact_layer/store.py`
and `api.py` never touch `lexical.py`/`vector_store.py`/`embeddings.py`
directly (section 9's "do not expose vector-database internals").

Persistence boundary (section 16): `fact_layer.store.Store` /
`data/store.json` are authoritative. Everything under a RetrievalConfig's
`index_dir` (lexical FTS5 db, vector store, embedding cache) is a derived,
rebuildable index — `rebuild_retrieval_index()` below reconstructs it from
scratch from a Store's facts, re-using the embedding cache so a rebuild
recomputes text/blocking freely but pays embedding cost only for text the
cache hasn't already seen.

Incremental ingestion (section 6, 15): `upsert_facts()` only computes
retrieval text, embeddings and index rows for the facts it is given — it
never touches facts already indexed. `Store.ingest()` (fact_layer/store.py)
calls this with just the newly-resolved facts from one document, so
uploading a new PDF costs retrieval-index work proportional to that PDF's
fact count, not the whole corpus.
"""

from __future__ import annotations

import threading
from typing import Iterable, Optional

from ..models import Fact
from .blocking import blocking_check
from .config import RetrievalConfig, load_config
from .embeddings import EmbeddingCache, EmbeddingProvider, FastEmbedProvider, embed_texts_cached
from .hybrid import CandidateMatch, HybridRetriever
from .lexical import LexicalIndex
from .text import fact_to_retrieval_text
from .vector_store import LocalNumpyVectorStore, VectorStore


def _fact_metadata(fact: Fact) -> dict:
    """Section 7's required metadata — enough to map a vector back to the
    fact without ever trusting the vector row as authoritative. Retrieval
    code must always resolve `fact_id` against `Store.facts` before acting
    on anything else in this dict (see index.py module docstring +
    security note in `fact_layer/retrieval/integration.py`)."""
    period_label = fact.qualifiers.period.label if fact.qualifiers.period else None
    return {
        "cluster_id": fact.cluster_key(),
        "canonical_subject": fact.subject,
        "canonical_measure": fact.measure,
        "issuer": fact.qualifiers.issuer,
        "value_kind": fact.value_kind.value,
        "period_label": period_label,
        "scope": fact.qualifiers.scope.value,
    }


class RetrievalIndex:
    def __init__(self, config: Optional[RetrievalConfig] = None) -> None:
        self.config = config or load_config()
        self.provider: EmbeddingProvider = FastEmbedProvider(
            model_name=self.config.embedding_model,
            cache_dir=self.config.embedding_model_cache_dir,
            mode=self.config.embedding_mode,
        )
        self.embedding_cache = EmbeddingCache(self.config.embedding_cache_path)
        self.lexical = LexicalIndex(self.config.lexical_db_path)
        # Vector store dimension is only knowable once the model has run
        # at least once; probing it eagerly here (rather than lazily on
        # first upsert) keeps LocalNumpyVectorStore's on-disk shape stable
        # from the very first write.
        dimension = self.provider.dimension()
        self.vectors: VectorStore = LocalNumpyVectorStore(
            self.config.vector_store_path, self.config.vector_meta_path, dimension,
        )
        self.hybrid = HybridRetriever(self.config.lexical_weight, self.config.semantic_weight)
        self._lock = threading.Lock()
        # cache_hits/misses this process — surfaced via /retrieval/stats.
        self.stats = {"embedding_cache_hits": 0, "embedding_cache_misses": 0, "last_rebuild": None}

    # ---- indexing -------------------------------------------------------

    def upsert_facts(self, facts: Iterable[Fact]) -> None:
        facts = list(facts)
        if not facts:
            return
        with self._lock:
            texts = {f.fact_id: fact_to_retrieval_text(f) for f in facts}
            before_hits = self.embedding_cache.size()
            vectors_by_text = embed_texts_cached(list(texts.values()), self.provider, self.embedding_cache)
            after_hits = self.embedding_cache.size()
            requested = len(set(texts.values()))
            newly_cached = after_hits - before_hits
            self.stats["embedding_cache_misses"] += newly_cached
            self.stats["embedding_cache_hits"] += requested - newly_cached

            self.lexical.upsert_many([(f.fact_id, texts[f.fact_id]) for f in facts])
            self.vectors.upsert_many([
                (f.fact_id, vectors_by_text[texts[f.fact_id]], _fact_metadata(f)) for f in facts
            ])
            self.vectors.save()

    def delete_fact(self, fact_id: str) -> None:
        with self._lock:
            self.lexical.delete(fact_id)
            self.vectors.delete(fact_id)
            self.vectors.save()

    def rebuild(self, facts: Iterable[Fact]) -> None:
        """Drop and fully reconstruct the lexical + vector indexes from
        the given facts (the authoritative FactStore's current facts).
        The embedding CACHE is intentionally not cleared — a rebuild is
        "re-derive the serving index", not "distrust every embedding ever
        computed"."""
        import time
        with self._lock:
            self.lexical.rebuild()
            self.vectors.rebuild()
        self.upsert_facts(facts)
        self.stats["last_rebuild"] = time.time()

    # ---- retrieval --------------------------------------------------------

    def _channel_matches(self, fact: Fact, fanout: int):
        """Raw per-channel matches (lexical, semantic), self-excluded,
        BEFORE fusion/truncation — shared by `retrieve_candidates()` and
        `candidate_funnel()` so there is exactly one place that owns the
        self-exclusion-before-normalization fix (see `retrieve_candidates()`'s
        docstring for why order matters here)."""
        query_text = fact_to_retrieval_text(fact)
        with self._lock:
            lexical_matches = [m for m in self.lexical.search(query_text, fanout) if m.fact_id != fact.fact_id]
            query_vector = embed_texts_cached([query_text], self.provider, self.embedding_cache)[query_text]
            semantic_matches = [m for m in self.vectors.search(query_vector, fanout) if m.fact_id != fact.fact_id]
        return lexical_matches, semantic_matches

    def retrieve_candidates(self, fact: Fact, top_k: Optional[int] = None) -> list[CandidateMatch]:
        """Returns the top-K hybrid-ranked candidates for `fact`, each
        annotated with its blocking status (section 9's CandidateMatch
        shape) — callers that want only pairs safe to adjudicate should
        filter to `blocking_status == "candidate"` (see
        `fact_layer/retrieval/integration.py`); callers building a
        diagnostic view (api.py's GET /facts/{fact_id}/candidates) show
        blocked ones too, with their reason, so the UI can say "retrieval
        found this, but it was blocked" rather than hiding it silently.

        Self-exclusion happens BEFORE fusion, not after: a query fact
        frequently retrieves itself as a near-perfect (score ~1.0) match,
        which would otherwise dominate each channel's min-max
        normalization range and compress every real candidate's
        normalized score toward zero — see `_channel_matches()`."""
        top_k = top_k or self.config.top_k
        # +1 fanout to absorb the query fact's own self-match without
        # shrinking the effective candidate pool.
        fanout = max(top_k, self.config.channel_fanout) + 1
        lexical_matches, semantic_matches = self._channel_matches(fact, fanout)
        candidates = self.hybrid.fuse(lexical_matches, semantic_matches)
        return candidates[:top_k]

    def candidate_funnel(self, fact: Fact, facts_by_id: dict[str, Fact], top_k: Optional[int] = None) -> dict:
        """Diagnostic view for api.py's GET /facts/{fact_id}/candidates and
        the frontend's compact "Candidate Retrieval" panel (section 19):
        how many candidates each stage of the pipeline actually produced,
        not just the final top-K. Never exposes lexical.py/vector_store.py
        objects — only plain counts and the same CandidateMatch list
        `retrieve_candidates()` already returns."""
        top_k = top_k or self.config.top_k
        fanout = max(top_k, self.config.channel_fanout) + 1
        lexical_matches, semantic_matches = self._channel_matches(fact, fanout)
        candidates = self.hybrid.fuse(lexical_matches, semantic_matches)
        self.annotate_blocking(fact, candidates, facts_by_id)
        after_block = [c for c in candidates if c.blocking_status == "candidate"]
        return {
            "lexical_count": len(lexical_matches),
            "semantic_count": len(semantic_matches),
            "after_block_count": len(after_block),
            "final_top_k_count": len(candidates[:top_k]),
            "candidates": candidates[:top_k],
        }

    def annotate_blocking(self, fact: Fact, candidates: list[CandidateMatch], facts_by_id: dict[str, Fact]) -> None:
        """Mutates each candidate's blocking_status/blocking_reason in
        place, resolving fact_id against the AUTHORITATIVE facts_by_id map
        (Store.facts) rather than trusting vector-store metadata — section
        20's "fact IDs must always resolve against authoritative Store
        data" requirement."""
        for c in candidates:
            other = facts_by_id.get(c.fact_id)
            if other is None:
                c.blocking_status, c.blocking_reason = "blocked", "UNKNOWN_FACT_ID"
                continue
            result = blocking_check(fact, other)
            c.blocking_status = "candidate" if result.candidate else "blocked"
            c.blocking_reason = result.reason

    def close(self) -> None:
        self.lexical.close()
        self.embedding_cache.close()

    def summary(self) -> dict:
        return {
            "indexed_facts": len(self.vectors),
            "embedding_model": self.provider.model_id(),
            "embedding_dimension": self.provider.dimension(),
            "lexical_index_size": len(self.lexical),
            "vector_index_size": len(self.vectors),
            "embedding_cache_size": self.embedding_cache.size(),
            "embedding_cache_hits": self.stats["embedding_cache_hits"],
            "embedding_cache_misses": self.stats["embedding_cache_misses"],
            "last_rebuild": self.stats["last_rebuild"],
            "configured_top_k": self.config.top_k,
            "configured_lexical_weight": self.config.lexical_weight,
            "configured_semantic_weight": self.config.semantic_weight,
            "retrieval_enabled": self.config.enabled,
        }


_INDEX_CACHE: dict[str, RetrievalIndex] = {}
_CACHE_LOCK = threading.Lock()


def get_index(config: Optional[RetrievalConfig] = None) -> RetrievalIndex:
    """Process-wide singleton per index_dir — constructing a RetrievalIndex
    loads/opens sqlite connections and (lazily) an ONNX model, so callers
    (Store.ingest(), api.py) should share one instance rather than each
    building their own."""
    cfg = config or load_config()
    with _CACHE_LOCK:
        idx = _INDEX_CACHE.get(cfg.index_dir)
        if idx is None:
            idx = RetrievalIndex(cfg)
            _INDEX_CACHE[cfg.index_dir] = idx
        return idx


def reset_index_cache() -> None:
    """Test-only escape hatch: forces the next get_index() to construct a
    fresh RetrievalIndex (e.g. after monkeypatching RETRIEVAL_INDEX_PATH
    to a temp dir)."""
    with _CACHE_LOCK:
        for idx in _INDEX_CACHE.values():
            idx.close()
        _INDEX_CACHE.clear()


def rebuild_retrieval_index(facts: Iterable[Fact], config: Optional[RetrievalConfig] = None) -> RetrievalIndex:
    """Section 16's rebuild_retrieval_index(): load authoritative facts,
    recreate retrieval representations, rebuild lexical index, recreate
    embeddings (cache-assisted), rebuild vector index. Returns the
    (now-populated) index."""
    idx = get_index(config)
    idx.rebuild(facts)
    return idx


__all__ = ["RetrievalIndex", "get_index", "reset_index_cache", "rebuild_retrieval_index"]
