"""Tests for fact_layer/retrieval/hybrid.py — score normalization + fusion.
Pure unit tests against hand-built LexicalMatch/VectorRecord lists, no
embedding model or index needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.retrieval.hybrid import HybridRetriever
from fact_layer.retrieval.lexical import LexicalMatch
from fact_layer.retrieval.vector_store import VectorRecord


def test_fusion_weights_are_applied():
    retriever = HybridRetriever(lexical_weight=1.0, semantic_weight=0.0)
    lexical = [LexicalMatch("a", 10.0), LexicalMatch("b", 1.0)]
    semantic = [VectorRecord("b", 0.99, {}), VectorRecord("a", 0.5, {})]
    out = retriever.fuse(lexical, semantic)
    # lexical_weight=1.0 means ranking must follow lexical order exactly.
    assert [c.fact_id for c in out] == ["a", "b"]


def test_fusion_normalizes_before_combining():
    # Raw BM25 scores (large numbers) must not simply swamp cosine scores
    # (bounded [-1, 1]) in a naive average — normalization brings both
    # channels to [0, 1] first.
    retriever = HybridRetriever(lexical_weight=0.5, semantic_weight=0.5)
    lexical = [LexicalMatch("a", 1000.0), LexicalMatch("b", 1.0)]
    semantic = [VectorRecord("b", 0.99, {}), VectorRecord("a", 0.01, {})]
    out = {c.fact_id: c for c in retriever.fuse(lexical, semantic)}
    assert out["a"].lexical_score == 1000.0     # raw score preserved for diagnostics
    assert out["a"].hybrid_score == 0.5 * 1.0 + 0.5 * 0.0    # a: top lexical, bottom semantic
    assert out["b"].hybrid_score == 0.5 * 0.0 + 0.5 * 1.0    # b: bottom lexical, top semantic


def test_candidate_found_by_only_one_channel_still_ranked():
    retriever = HybridRetriever(lexical_weight=0.45, semantic_weight=0.55)
    lexical = [LexicalMatch("only_lexical", 5.0)]
    semantic = [VectorRecord("only_semantic", 0.8, {})]
    out = {c.fact_id: c for c in retriever.fuse(lexical, semantic)}
    assert out["only_lexical"].semantic_score == 0.0
    assert out["only_lexical"].semantic_rank is None
    assert out["only_semantic"].lexical_score == 0.0
    assert out["only_semantic"].lexical_rank is None


def test_ranks_are_one_indexed_and_preserved():
    retriever = HybridRetriever(0.5, 0.5)
    lexical = [LexicalMatch("a", 3.0), LexicalMatch("b", 2.0), LexicalMatch("c", 1.0)]
    semantic = []
    out = {c.fact_id: c for c in retriever.fuse(lexical, semantic)}
    assert out["a"].lexical_rank == 1
    assert out["b"].lexical_rank == 2
    assert out["c"].lexical_rank == 3


def test_single_candidate_gets_full_normalized_score():
    retriever = HybridRetriever(0.5, 0.5)
    out = retriever.fuse([LexicalMatch("solo", 0.0001)], [VectorRecord("solo", 0.5, {})])
    assert out[0].hybrid_score == 1.0   # min==max degenerate case must not divide by zero / zero out


def test_output_sorted_descending_by_hybrid_score():
    retriever = HybridRetriever(0.5, 0.5)
    lexical = [LexicalMatch("low", 1.0), LexicalMatch("high", 100.0), LexicalMatch("mid", 50.0)]
    out = retriever.fuse(lexical, [])
    scores = [c.hybrid_score for c in out]
    assert scores == sorted(scores, reverse=True)
