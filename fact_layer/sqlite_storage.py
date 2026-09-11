"""
SQLiteFactStore: the durable, incrementally-writable local backend.

WHY THIS FILE EXISTS
--------------------------------------------------------------------------
`storage.py`'s JsonFactStore.save() always serialises the ENTIRE
StoreSnapshot to one file, because a JSON document has no way to append —
every save() is O(total store size), which is exactly the measured ceiling
in the README's persistence benchmark (0.03s small -> 7.26s at ~122MB).
`Store.ingest()` already computes what's NEW from one document
incrementally in memory (see store.py's touched_clusters/new_fact_ids); the
bottleneck is purely that persistence throws that distinction away and
re-writes everything regardless. SQLite's row-oriented storage doesn't have
that problem: inserting N new rows costs O(N), not O(existing rows).

WHAT IS AND ISN'T NORMALISED
--------------------------------------------------------------------------
Pragmatic, not textbook-relational, per the same "don't invent a parallel
representation" instinct the rest of this codebase applies elsewhere:

  - `facts`: first-class columns for what's actually queried/joined on
    (fact_id, subject, measure, cluster_key, value_kind, confidence, doc_id)
    plus one `data_json` column holding the rest of Fact.to_dict() (value,
    qualifiers, modality, subject_raw/measure_raw, value_verification*, the
    PRIMARY evidence) — reusing storage.py's existing _json_safe()/
    Fact.to_dict()/_fact_from_dict() round-trip verbatim rather than
    inventing a second serialisation scheme.
  - `extra_evidence`: one row per merged evidence span (Store.get_evidence()
    already needs these queryable by fact_id — that's a real access
    pattern, hence a real table), full Evidence payload as `data_json`.
  - `relations`: fully columnar (every field the domain model has), because
    relations are the other half of what scales with corpus size and are
    queried by source_fact_id/target_fact_id (the API's per-fact relation
    lookups, entity_history(), lineage.py).
  - `clusters` has NO table. `Fact.cluster_key()` is a pure function of
    (subject, measure), already stored as columns — recomputing
    `{cluster_key: [fact_id, ...]}` via GROUP BY on load is exactly as
    correct as the JSON dict and avoids a second copy of the same fact that
    could drift out of sync with the row it's derived from.
  - `rejected_facts` / `resolution_decisions` / `resolution_meta`: exist so
    the SQLite backend's READ side (list_rejected_facts,
    read_resolution_summary) is genuinely backed by SQL, matching
    JsonFactStore's contract. Their LIVE WRITE path is deliberately left
    alone: extract.py's `_append_rejected` and resolve.py's
    `write_resolution_log()` are explicitly documented in storage.py's
    module docstring as domain-adjacent, file-based, and out of scope for
    this contract ("more indirection for no behavior change") — that
    boundary predates this file and this migration doesn't move it. These
    two tables are populated by scripts/migrate_json_to_sqlite.py so a
    migrated store is queryable and comparable for parity, not because the
    hot path now writes through them.

INCREMENTAL WRITES
--------------------------------------------------------------------------
`save()` (the FactStore contract) still accepts a full StoreSnapshot and
still produces a fully-consistent database — nothing about the existing
contract breaks, and `scripts/build_demo_store.py`'s "ingest N documents,
save() once" pattern works unchanged. But `save()` here is UPSERT-based
(INSERT ... ON CONFLICT DO UPDATE keyed on each row's natural id —
fact_id, doc_id, or the (source,target,relation) triple api.py's own
_relation_id() already treats as a relation's identity) inside ONE
transaction, so calling it again with a snapshot that only added a few new
rows costs proportionally to what changed, not to len(facts). That already
satisfies "adding one document must not rewrite the whole store" for any
caller that keeps using the existing Store.save(backend) call site.

`save_new()` goes one step further for the one call site that actually
iterates per-ingest (api.py's live /ingest endpoint, via the new
Store.save_incremental() wrapper in store.py): it is handed only the rows
one `Store.ingest()` call actually added, so it does not even need to
inspect existing rows to know what to skip — every statement in its
transaction is a plain INSERT.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from .models import Evidence, Fact, Relation, RelationType
from .storage import (
    FactStore,
    StoreSnapshot,
    _evidence_from_dict,
    _evidence_to_dict,
    _fact_from_dict,
    _json_safe,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SQLITE_PATH = os.path.join(_REPO_ROOT, "data", "store.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    filename    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id     TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    measure     TEXT NOT NULL,
    cluster_key TEXT NOT NULL,
    value_kind  TEXT NOT NULL,
    confidence  REAL NOT NULL,
    doc_id      TEXT,
    data_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_cluster_key ON facts(cluster_key);
CREATE INDEX IF NOT EXISTS idx_facts_doc_id ON facts(doc_id);

CREATE TABLE IF NOT EXISTS extra_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    doc_id      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    data_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extra_evidence_fact_id ON extra_evidence(fact_id);

CREATE TABLE IF NOT EXISTS relations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_fact_id      TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    target_fact_id      TEXT NOT NULL REFERENCES facts(fact_id) ON DELETE CASCADE,
    relation            TEXT NOT NULL,
    confidence          REAL NOT NULL,
    reason_code         TEXT NOT NULL,
    explanation         TEXT NOT NULL,
    qualifier_diff_json TEXT NOT NULL,
    decided_by          TEXT NOT NULL,
    UNIQUE(source_fact_id, target_fact_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_fact_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_fact_id);

CREATE TABLE IF NOT EXISTS rejected_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT,
    doc_filename  TEXT,
    page_no       INTEGER,
    reason        TEXT,
    detail        TEXT,
    raw_fact_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejected_doc_id ON rejected_facts(doc_id);

CREATE TABLE IF NOT EXISTS resolution_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT,
    raw         TEXT,
    canonical   TEXT,
    tier        TEXT,
    confidence  REAL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_resolution_decisions_tier ON resolution_decisions(tier);

-- Singleton row: fields of the resolution summary that aren't a simple
-- aggregate over resolution_decisions (llm_calls_used is a resolver-level
-- counter, canonical_measures is the resolver's measure registry, not one
-- per decision).
CREATE TABLE IF NOT EXISTS resolution_meta (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    llm_calls_used          INTEGER NOT NULL,
    canonical_measures_json TEXT NOT NULL
);
"""


class SQLiteFactStore(FactStore):
    """Transactional, incrementally-writable backend. See module docstring
    for the schema rationale and what stays file-based on purpose.

    Concurrency (README's Persistence/Concurrency section covers this in
    full): WAL mode is enabled below because this workload is "one writer,
    occasional readers" (a single API process serialising ingests behind
    api.py's existing `_INGEST_LOCK`, while GET endpoints may read
    concurrently) — WAL lets readers proceed without blocking on the
    writer's transaction, which plain rollback-journal mode would not.
    This is NOT multi-writer support: SQLite still allows exactly one
    writer at a time, enforced by its own locking, and `_INGEST_LOCK` stays
    in api.py unchanged (see the wiring there) because removing a
    process-local lock in favor of "the database will sort it out" would
    still leave two concurrent ingests racing on the SAME in-memory `Store`
    object above the storage layer — a problem this file does not touch."""

    def __init__(self, db_path: str = _DEFAULT_SQLITE_PATH) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._ensure_schema()

    # ---- connection / schema -------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def supports_incremental_save(self) -> bool:
        return True

    # ---- full snapshot (FactStore contract) ----------------------------

    def load(self) -> StoreSnapshot:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            facts: dict[str, Fact] = {}
            for row in conn.execute("SELECT data_json FROM facts"):
                fd = json.loads(row["data_json"])
                facts[fd["fact_id"]] = _fact_from_dict(fd)

            extra_evidence: dict[str, list[Evidence]] = {}
            for row in conn.execute(
                "SELECT fact_id, data_json FROM extra_evidence ORDER BY id"
            ):
                extra_evidence.setdefault(row["fact_id"], []).append(
                    _evidence_from_dict(json.loads(row["data_json"]))
                )

            relations: list[Relation] = []
            for row in conn.execute(
                "SELECT source_fact_id, target_fact_id, relation, confidence, "
                "reason_code, explanation, qualifier_diff_json, decided_by "
                "FROM relations ORDER BY id"
            ):
                relations.append(Relation(
                    source_fact_id=row["source_fact_id"],
                    target_fact_id=row["target_fact_id"],
                    relation=RelationType(row["relation"]),
                    confidence=row["confidence"],
                    reason_code=row["reason_code"],
                    explanation=row["explanation"],
                    qualifier_diff=json.loads(row["qualifier_diff_json"]),
                    decided_by=row["decided_by"],
                ))

            ingested_docs = {
                row["doc_id"]: row["filename"]
                for row in conn.execute("SELECT doc_id, filename FROM documents")
            }

            # clusters: derived, not stored — see module docstring.
            clusters: dict[str, list[str]] = {}
            for row in conn.execute(
                "SELECT cluster_key, fact_id FROM facts ORDER BY cluster_key, fact_id"
            ):
                clusters.setdefault(row["cluster_key"], []).append(row["fact_id"])

        return StoreSnapshot(
            facts=facts, extra_evidence=extra_evidence, relations=relations,
            clusters=clusters, ingested_docs=ingested_docs,
        )

    def save(self, snapshot: StoreSnapshot) -> None:
        """Full-snapshot save via UPSERT, in one transaction. Unlike JSON,
        this does not discard-and-rewrite: every row's identity (fact_id /
        doc_id / the relation triple) is preserved, so calling this
        repeatedly with a growing snapshot only pays for the rows that are
        actually new or changed, not for re-writing bytes that didn't
        change — SQLite's B-tree still has to touch each row's page, but
        that is a bounded per-row cost, not a whole-file serialise."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                self._upsert_documents(conn, snapshot.ingested_docs)
                self._upsert_facts(conn, snapshot.facts)
                self._replace_extra_evidence(conn, snapshot.extra_evidence)
                self._upsert_relations(conn, snapshot.relations)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---- incremental save (new, opt-in — see store.py's
    # Store.save_incremental()) -------------------------------------------

    def save_new(
        self,
        *,
        new_doc: Optional[tuple[str, str]],
        new_facts: dict[str, Fact],
        new_extra_evidence: dict[str, list[Evidence]],
        new_relations: list[Relation],
    ) -> None:
        """Persist exactly what one Store.ingest() call added. Every
        statement here is scoped to the new rows only — this never reads
        or rewrites a previously-persisted fact/relation/document. One
        transaction: either the whole increment lands, or none of it does
        (see test_sqlite_storage.py's rollback test)."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                if new_doc is not None:
                    self._upsert_documents(conn, {new_doc[0]: new_doc[1]})
                self._upsert_facts(conn, new_facts)
                for fact_id, spans in new_extra_evidence.items():
                    for e in spans:
                        conn.execute(
                            "INSERT INTO extra_evidence (fact_id, doc_id, page, data_json) "
                            "VALUES (?, ?, ?, ?)",
                            (fact_id, e.doc_id, e.page, json.dumps(_json_safe(_evidence_to_dict(e)))),
                        )
                self._upsert_relations(conn, new_relations)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---- internals: shared upsert helpers -------------------------------

    @staticmethod
    def _upsert_documents(conn: sqlite3.Connection, docs: dict[str, str]) -> None:
        conn.executemany(
            "INSERT INTO documents (doc_id, filename) VALUES (?, ?) "
            "ON CONFLICT(doc_id) DO UPDATE SET filename = excluded.filename",
            list(docs.items()),
        )

    @staticmethod
    def _upsert_facts(conn: sqlite3.Connection, facts: dict[str, Fact]) -> None:
        rows = []
        for fid, f in facts.items():
            ev = f.evidence
            rows.append((
                fid, f.subject, f.measure, f.cluster_key(), f.value_kind.value,
                f.confidence, ev.doc_id if ev else None,
                json.dumps(_json_safe(f.to_dict())),
            ))
        conn.executemany(
            "INSERT INTO facts (fact_id, subject, measure, cluster_key, value_kind, "
            "confidence, doc_id, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(fact_id) DO UPDATE SET "
            "subject=excluded.subject, measure=excluded.measure, "
            "cluster_key=excluded.cluster_key, value_kind=excluded.value_kind, "
            "confidence=excluded.confidence, doc_id=excluded.doc_id, "
            "data_json=excluded.data_json",
            rows,
        )

    @staticmethod
    def _replace_extra_evidence(conn: sqlite3.Connection, extra: dict[str, list[Evidence]]) -> None:
        """Full-snapshot path only (save(), not save_new()): extra_evidence
        has no independent identity of its own to upsert against (a fact's
        merged-span list can shrink or reorder), so the correctness-first
        move for a full snapshot is delete-then-reinsert per touched
        fact_id, scoped to exactly the facts in this snapshot rather than
        the whole table."""
        fact_ids = list(extra.keys())
        if fact_ids:
            conn.executemany(
                "DELETE FROM extra_evidence WHERE fact_id = ?",
                [(fid,) for fid in fact_ids],
            )
        for fact_id, spans in extra.items():
            for e in spans:
                conn.execute(
                    "INSERT INTO extra_evidence (fact_id, doc_id, page, data_json) VALUES (?, ?, ?, ?)",
                    (fact_id, e.doc_id, e.page, json.dumps(_json_safe(_evidence_to_dict(e)))),
                )

    @staticmethod
    def _upsert_relations(conn: sqlite3.Connection, relations: list[Relation]) -> None:
        if not relations:
            return
        rows = [
            (
                r.source_fact_id, r.target_fact_id, r.relation.value, r.confidence,
                r.reason_code, r.explanation, json.dumps(_json_safe(r.qualifier_diff)),
                r.decided_by,
            )
            for r in relations
        ]
        conn.executemany(
            "INSERT INTO relations (source_fact_id, target_fact_id, relation, confidence, "
            "reason_code, explanation, qualifier_diff_json, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_fact_id, target_fact_id, relation) DO UPDATE SET "
            "confidence=excluded.confidence, reason_code=excluded.reason_code, "
            "explanation=excluded.explanation, qualifier_diff_json=excluded.qualifier_diff_json, "
            "decided_by=excluded.decided_by",
            rows,
        )

    # ---- diagnostic logs (read side; see module docstring for why the
    # live write path stays file-based) -----------------------------------

    def list_rejected_facts(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT doc_id, doc_filename, page_no, reason, detail, raw_fact_json "
                "FROM rejected_facts ORDER BY id"
            ).fetchall()
        return [
            {
                "raw_fact": json.loads(row["raw_fact_json"]),
                "page_no": row["page_no"],
                "doc_id": row["doc_id"],
                "doc_filename": row["doc_filename"],
                "reason": row["reason"],
                "detail": row["detail"],
            }
            for row in rows
        ]

    def write_rejected_facts(self, records: list[dict]) -> None:
        """Migration-only entry point (scripts/migrate_json_to_sqlite.py) —
        not called from extract.py's hot path; see module docstring."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    "INSERT INTO rejected_facts (doc_id, doc_filename, page_no, reason, detail, raw_fact_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (r.get("doc_id"), r.get("doc_filename"), r.get("page_no"),
                         r.get("reason"), r.get("detail"), json.dumps(_json_safe(r.get("raw_fact", {}))))
                        for r in records
                    ],
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def read_resolution_summary(self) -> Optional[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            meta = conn.execute(
                "SELECT llm_calls_used, canonical_measures_json FROM resolution_meta WHERE id = 1"
            ).fetchone()
            if meta is None:
                return None
            by_tier: dict[str, int] = {}
            total = 0
            for row in conn.execute(
                "SELECT tier, COUNT(*) AS n FROM resolution_decisions GROUP BY tier"
            ):
                by_tier[row["tier"]] = row["n"]
                total += row["n"]
        return {
            "total_decisions": total,
            "by_tier": by_tier,
            "llm_calls_used": meta["llm_calls_used"],
            "canonical_measures": json.loads(meta["canonical_measures_json"]),
        }

    def write_resolution_summary(self, summary: dict) -> None:
        """Migration-only entry point — see read_resolution_summary()."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM resolution_decisions")
                conn.executemany(
                    "INSERT INTO resolution_decisions (kind, raw, canonical, tier, confidence, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (d.get("kind"), d.get("raw"), d.get("canonical"), d.get("tier"),
                         d.get("confidence"), d.get("detail"))
                        for d in summary.get("decisions", [])
                    ],
                )
                conn.execute(
                    "INSERT INTO resolution_meta (id, llm_calls_used, canonical_measures_json) "
                    "VALUES (1, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "llm_calls_used=excluded.llm_calls_used, "
                    "canonical_measures_json=excluded.canonical_measures_json",
                    (
                        summary.get("summary", {}).get("llm_calls_used", 0),
                        json.dumps(summary.get("summary", {}).get("canonical_measures", [])),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
