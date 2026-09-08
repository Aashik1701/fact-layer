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
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional


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
        # Pragmas, not schema: this index is a derived, fully rebuildable
        # artifact (see index.py's persistence boundary docstring), so
        # durability of individual writes buys nothing — a torn index is
        # rebuilt from Store.facts(), never repaired. WAL + NORMAL removes
        # an fsync per commit, which is the dominant cost when upsert_many()
        # is called once per document during a bulk ingest.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the FTS5 table, migrating a pre-`block_key` index by
        dropping it. Safe because this table is derived data: index.py's
        `rebuild()`/`upsert_facts()` repopulate it from the authoritative
        Store, so the worst case of a migration is one re-index, never
        data loss."""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='facts_fts'"
        ).fetchone()
        if row is not None and "block_key" not in (row[0] or ""):
            self._conn.execute("DROP TABLE facts_fts")
            self._conn.commit()
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
            " fact_id UNINDEXED, block_key, retrieval_text"
            ")"
        )
        self._conn.commit()

    def upsert(self, fact_id: str, retrieval_text: str, block_key: str = "") -> None:
        self.upsert_many([(fact_id, retrieval_text, block_key)])

    def upsert_many(self, items: list[tuple]) -> None:
        """`items` are (fact_id, retrieval_text, block_key) triples.

        Two-element (fact_id, retrieval_text) tuples are still accepted and
        indexed with an empty block_key: the bucket column is a retrieval
        *cost* key, so a row without one simply never matches a
        bucket-filtered query — it stays fully searchable by an unfiltered
        `search()`. Keeping the old arity working means this index remains
        usable standalone (and keeps existing callers/tests valid) instead
        of the schema change rippling outward."""
        if not items:
            return
        rows = [(it[0], it[1], it[2] if len(it) > 2 else "") for it in items]
        fact_ids = [fid for fid, _, _ in rows]
        placeholders = ",".join("?" for _ in fact_ids)
        self._conn.execute(f"DELETE FROM facts_fts WHERE fact_id IN ({placeholders})", fact_ids)
        self._conn.executemany(
            "INSERT INTO facts_fts (fact_id, block_key, retrieval_text) VALUES (?, ?, ?)",
            [(fid, bk, text) for fid, text, bk in rows],
        )
        self._conn.commit()

    def delete(self, fact_id: str) -> None:
        self._conn.execute("DELETE FROM facts_fts WHERE fact_id = ?", (fact_id,))
        self._conn.commit()

    def search(self, query_text: str, top_k: int, block_key: Optional[str] = None) -> list[LexicalMatch]:
        # FTS5's MATCH syntax treats punctuation specially; the retrieval
        # text is our own deterministic "key: value" lines, so quote each
        # token defensively rather than hand-roll query escaping.
        #
        # _STRUCTURAL_STOPWORDS are excluded from the QUERY side only —
        # the index itself is untouched (FTS5 tokenizes whatever text
        # upsert_many() stores; nothing here controls that). Every
        # fact_to_retrieval_text() output repeats the same field-name
        # tokens ("subject", "measure", "scope", ...) and the same "none"
        # placeholder for an absent qualifier, so those terms have a
        # posting list roughly as long as the whole corpus. Left in the
        # OR-query, BM25 has to score every row for those non-
        # discriminating terms on every single search — cost that grows
        # with corpus size on every one of the N per-fact queries this
        # module's caller issues, i.e. O(N^2) overall. Dropping them
        # leaves only the actual discriminating values (subject/measure
        # names, period labels, real qualifier values), which is both
        # faster and a more meaningful lexical signal.
        tokens = [t for t in _tokenize(query_text) if t and t not in _STRUCTURAL_STOPWORDS]
        if not tokens:
            return []
        match_query = " OR ".join(f'"{t}"' for t in tokens)
        if block_key:
            # Column-scoped FTS5 filter, ANDed into the MATCH expression so
            # sqlite intersects posting lists using the index rather than
            # BM25-scoring the whole corpus and discarding afterwards.
            # `block_key` is a 16-char hex token from blocking.block_key(),
            # so it needs no escaping and cannot inject query syntax.
            #
            # Semantically this is a no-op: bucket equality IS
            # blocking_check(), so every row excluded here is one
            # annotate_blocking() would have marked "blocked".
            match_query = f'block_key:"{block_key}" AND ({match_query})'
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


# The fixed field-name vocabulary fact_to_retrieval_text() emits on every
# single fact, plus its "absent qualifier" placeholder — see search()'s
# comment for why these are excluded from the query rather than left to
# inflate every BM25 evaluation.
_STRUCTURAL_STOPWORDS = frozenset({
    "subject", "measure", "value_kind", "period", "scope", "segment",
    "geography", "issuer", "basis", "modality", "extra", "none",
    "block_key",
})


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Must split on the SAME boundaries FTS5's default (unicode61)
    tokenizer uses when it indexes `upsert_many()`'s stored text, or a
    query token can silently never match anything.

    The previous version stripped punctuation out of each whitespace-
    separated word instead of splitting on it — "FY2023-24" became the
    glued token "fy202324", but FTS5 itself indexes "FY2023-24" as the
    two separate tokens "fy2023" and "24" (confirmed against a live FTS5
    table), so that glued query token matched nothing. Every period label
    in this project uses a hyphen or slash (`FY2023-24`, `Q1 FY2023-24`),
    so this was silently zeroing out the lexical channel's period signal
    for every single query — regex-splitting on alnum/underscore runs
    (matching unicode61's own separator behavior) fixes it."""
    return _TOKEN_RE.findall(text.lower())


__all__ = ["LexicalIndex", "LexicalMatch"]
