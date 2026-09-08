"""
Hybrid retrieval: fuse the lexical (BM25) and semantic (cosine) channels
into one ranked candidate list.

BM25 scores and cosine similarities live on different, incomparable
scales (BM25 is an unbounded positive real; cosine is in [-1, 1]) — this
module never averages them raw. Each channel is min-max normalized to
[0, 1] over the candidates *that channel itself returned* before the
weighted sum, per the task's explicit requirement. A fact retrieved by
only one channel gets 0.0 for the other, not a missing value — it still
gets ranked, just without that channel's support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .lexical import LexicalMatch
from .vector_store import VectorRecord


@dataclass
class CandidateMatch:
    fact_id: str
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    hybrid_score: float = 0.0
    lexical_rank: Optional[int] = None      # 1-indexed; None if not retrieved lexically
    semantic_rank: Optional[int] = None     # 1-indexed; None if not retrieved semantically
    blocking_status: str = "candidate"      # "candidate" | "blocked"
    blocking_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "lexical_score": round(self.lexical_score, 6),
            "semantic_score": round(self.semantic_score, 6),
            "hybrid_score": round(self.hybrid_score, 6),
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "blocking_status": self.blocking_status,
            "blocking_reason": self.blocking_reason,
        }


def _min_max_normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        # Every candidate scored identically (including the common
        # single-candidate case) — treat as maximal support rather than
        # dividing by zero or silently zeroing out a real match.
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridRetriever:
    def __init__(self, lexical_weight: float, semantic_weight: float) -> None:
        self.lexical_weight = lexical_weight
        self.semantic_weight = semantic_weight

    def fuse(
        self, lexical_matches: list[LexicalMatch], semantic_matches: list[VectorRecord],
    ) -> list[CandidateMatch]:
        lexical_raw = {m.fact_id: m.score for m in lexical_matches}
        semantic_raw = {m.fact_id: m.score for m in semantic_matches}
        lexical_norm = _min_max_normalize(lexical_raw)
        semantic_norm = _min_max_normalize(semantic_raw)

        lexical_rank = {m.fact_id: i + 1 for i, m in enumerate(lexical_matches)}
        semantic_rank = {m.fact_id: i + 1 for i, m in enumerate(semantic_matches)}

        all_ids = list(dict.fromkeys([*lexical_raw.keys(), *semantic_raw.keys()]))
        out: list[CandidateMatch] = []
        for fid in all_ids:
            l_score = lexical_norm.get(fid, 0.0)
            s_score = semantic_norm.get(fid, 0.0)
            hybrid = self.lexical_weight * l_score + self.semantic_weight * s_score
            out.append(CandidateMatch(
                fact_id=fid,
                lexical_score=lexical_raw.get(fid, 0.0),
                semantic_score=semantic_raw.get(fid, 0.0),
                hybrid_score=hybrid,
                lexical_rank=lexical_rank.get(fid),
                semantic_rank=semantic_rank.get(fid),
            ))
        out.sort(key=lambda c: c.hybrid_score, reverse=True)
        return out


__all__ = ["CandidateMatch", "HybridRetriever"]
