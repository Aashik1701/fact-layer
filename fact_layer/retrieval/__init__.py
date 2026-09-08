"""
Retrieval + Scale layer: candidate generation in front of the existing,
unmodified comparability gate and relationship adjudicator.

    ALL FACTS
      -> deterministic candidate blocking      (blocking.py)
      -> lexical retrieval (BM25/FTS5)         (lexical.py)
      -> semantic retrieval (embeddings)       (embeddings.py, vector_store.py)
      -> hybrid candidate ranking              (hybrid.py)
      -> top-K candidates                      (index.py, candidates())
      -> existing comparability gate           (fact_layer.comparability, UNCHANGED)
      -> existing relationship adjudication    (fact_layer.adjudicate, UNCHANGED)

This package never decides comparable/incomparable/corroborates/contradicts/
apparent_conflict/supersedes/aggregates itself — it only narrows which pairs
of facts are worth handing to `fact_layer.comparability.gate()` and
`fact_layer.adjudicate.adjudicate()`, which remain the sole authority for
those verdicts. See `fact_layer/retrieval/blocking.py`'s module docstring
for exactly which pairs blocking is (and, more importantly, is NOT) allowed
to remove, and why.

Public surface (everything else in this package is an implementation
detail, per the "do not expose vector-database internals" requirement):
    - RetrievalConfig, load_config()      (config.py)
    - fact_to_retrieval_text(fact)         (text.py)
    - CandidateMatch, retrieve_candidates() (candidates below, via index.py)
    - RetrievalIndex, rebuild_retrieval_index() (index.py)
    - generate_candidate_pairs()           (integration.py)
"""

from __future__ import annotations

from .blocking import BlockingResult, blocking_check
from .config import RetrievalConfig, load_config
from .hybrid import CandidateMatch
from .index import RetrievalIndex, get_index, rebuild_retrieval_index, reset_index_cache
from .integration import adjudicate_via_retrieval, generate_candidate_pairs
from .text import fact_to_retrieval_text

__all__ = [
    "RetrievalConfig",
    "load_config",
    "fact_to_retrieval_text",
    "BlockingResult",
    "blocking_check",
    "CandidateMatch",
    "RetrievalIndex",
    "get_index",
    "rebuild_retrieval_index",
    "reset_index_cache",
    "generate_candidate_pairs",
    "adjudicate_via_retrieval",
]


def retrieve_candidates(fact, index: "RetrievalIndex", top_k: int | None = None):
    """Candidate generation API (section 9): the one entry point downstream
    code (api.py's diagnostic endpoint, integration.py) should call. Never
    reaches into index.lexical/index.vectors directly."""
    return index.retrieve_candidates(fact, top_k=top_k)
