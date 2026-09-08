"""
Lexical retrieval: sqlite3 FTS5 with its built-in `bm25()` ranking
function. No new dependency — verified in this environment that the
project's Python build has FTS5 compiled into its stdlib `sqlite3`
module. This is the "lightweight local implementation" the task asks for,
scoped to what this project actually needs (tokenized canonical
subject/measure/qualifier text — see `fact_layer.retrieval.text
.fact_to_retrieval_text()` — never raw evidence).

FTS5's `bm25()` returns a NEGATIVE score where more negative = better
match (lower "distance"); this module negates it once here so every
caller of `LexicalIndex.search()` sees "higher score = more relevant",
matching the semantic channel's cosine convention and sparing
`hybrid.py` from carrying that inversion.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass


@dataclass
class LexicalMatch:
    fact_id: str
    score: float


class LexicalIndex:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # check_same_thread=False: LexicalIndex lives inside the
        # process-wide RetrievalIndex singleton (index.py's get_index()),
        # which api.py calls from FastAPI request-handler threads and
        # BackgroundTasks worker threads alike. Actual thread-safety comes
        # from RetrievalIndex._lock serializing every call into this
        # object, not from sqlite3 itself — same "one lock, single local
        # process" model api.py's own _INGEST_LOCK already uses.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
            " fact_id UNINDEXED, retrieval_text"
            ")"
        )
        self._conn.commit()

    def upsert(self, fact_id: str, retrieval_text: str) -> None:
        self.upsert_many([(fact_id, retrieval_text)])

    def upsert_many(self, items: list[tuple[str, str]]) -> None:
        if not items:
            return
        fact_ids = [fid for fid, _ in items]
        placeholders = ",".join("?" for _ in fact_ids)
        self._conn.execute(f"DELETE FROM facts_fts WHERE fact_id IN ({placeholders})", fact_ids)
        self._conn.executemany("INSERT INTO facts_fts (fact_id, retrieval_text) VALUES (?, ?)", items)
        self._conn.commit()

    def delete(self, fact_id: str) -> None:
        self._conn.execute("DELETE FROM facts_fts WHERE fact_id = ?", (fact_id,))
        self._conn.commit()

    def search(self, query_text: str, top_k: int) -> list[LexicalMatch]:
        # FTS5's MATCH syntax treats punctuation specially; the retrieval
        # text is our own deterministic "key: value" lines, so quote each
        # token defensively rather than hand-roll query escaping.
        tokens = [t for t in _tokenize(query_text) if t]
        if not tokens:
            return []
        match_query = " OR ".join(f'"{t}"' for t in tokens)
        rows = self._conn.execute(
            "SELECT fact_id, bm25(facts_fts) AS rank FROM facts_fts "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
            (match_query, top_k),
        ).fetchall()
        return [LexicalMatch(fact_id=fid, score=-rank) for fid, rank in rows]

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0]

    def rebuild(self) -> None:
        self._conn.execute("DELETE FROM facts_fts")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _tokenize(text: str) -> list[str]:
    out = []
    for raw in text.replace(":", " ").replace("\n", " ").split(" "):
        t = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
        if t:
            out.append(t.lower())
    return out


__all__ = ["LexicalIndex", "LexicalMatch"]
