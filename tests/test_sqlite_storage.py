"""
SQLiteFactStore — parity with JsonFactStore, and SQLite-specific regression
coverage (transactions, foreign keys, incremental writes, reopening).

Every test here uses an isolated tempfile database or JSON file — nothing
in this file reads or writes data/store.json, data/rejected_facts.jsonl, or
data/resolution_log.json. See scripts/migrate_json_to_sqlite.py for the
tool that operates on the real committed files (never run from a test).
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Relation,
    RelationType, Scope, ValueKind,
)
from fact_layer.sqlite_storage import SQLiteFactStore
from fact_layer.storage import JsonFactStore, StoreSnapshot
from fact_layer.store import Store


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "store.sqlite3")


def _fact(subject="acme", measure="revenue", val="100", doc_id="d1", page=1,
          char_start=0, scope=Scope.STANDALONE, period_label="FY2023-24") -> Fact:
    period = Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label=period_label)
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(val), unit="currency", currency="INR", sig_figs=4, raw=val),
        qualifiers=Qualifiers(period=period, scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=char_start, char_end=char_start + len(val),
                          verbatim_quote=val, verified=True),
    )


def _snapshot_with(*facts: Fact, relations=None, docs=None) -> StoreSnapshot:
    """Mirrors exactly what the real Store.save() always passes: `clusters`
    derived from the given facts' own cluster_key(), the same bookkeeping
    Store.ingest() keeps in sync — not an independent, possibly-stale copy.
    An empty/inconsistent `clusters` dict here would be a snapshot no real
    Store ever produces, so testing against one wouldn't prove anything
    about the two backends' real-world parity."""
    clusters: dict[str, list[str]] = {}
    for f in facts:
        clusters.setdefault(f.cluster_key(), []).append(f.fact_id)
    return StoreSnapshot(
        facts={f.fact_id: f for f in facts},
        extra_evidence={},
        relations=relations or [],
        clusters=clusters,
        ingested_docs=docs or {"d1": "doc1.pdf"},
    )


# --------------------------------------------------------------------------
# 1. Empty database
# --------------------------------------------------------------------------

def test_empty_database_loads_empty_snapshot(db_path):
    store = SQLiteFactStore(db_path)
    snap = store.load()
    assert snap.facts == {} and snap.relations == [] and snap.ingested_docs == {}
    assert store.list_rejected_facts() == []
    assert store.read_resolution_summary() is None


# --------------------------------------------------------------------------
# 2 & 3. First ingestion, then a second incremental ingestion
# --------------------------------------------------------------------------

def test_first_then_second_incremental_ingestion_both_persist(db_path):
    store = SQLiteFactStore(db_path)
    f1 = _fact(subject="acme", measure="revenue", val="100", doc_id="d1")
    store.save_new(new_doc=("d1", "doc1.pdf"), new_facts={f1.fact_id: f1},
                    new_extra_evidence={}, new_relations=[])
    assert len(store.load().facts) == 1

    f2 = _fact(subject="other", measure="ebitda", val="50", doc_id="d2")
    store.save_new(new_doc=("d2", "doc2.pdf"), new_facts={f2.fact_id: f2},
                    new_extra_evidence={}, new_relations=[])
    snap = store.load()
    assert len(snap.facts) == 2
    assert f1.fact_id in snap.facts and f2.fact_id in snap.facts
    assert snap.ingested_docs == {"d1": "doc1.pdf", "d2": "doc2.pdf"}


def test_incremental_save_does_not_touch_previously_persisted_rows(db_path):
    """The architectural point of this whole file: adding a second document
    must not rewrite the first one's rows. Verified here via SQLite's own
    rowid, which is stable across UPSERTs that don't touch a row and would
    change (or the row would be a new insert) if the first fact's row had
    been deleted/reinserted."""
    store = SQLiteFactStore(db_path)
    f1 = _fact(doc_id="d1")
    store.save_new(new_doc=("d1", "doc1.pdf"), new_facts={f1.fact_id: f1},
                    new_extra_evidence={}, new_relations=[])
    with sqlite3.connect(db_path) as conn:
        rowid_before = conn.execute(
            "SELECT rowid FROM facts WHERE fact_id = ?", (f1.fact_id,)
        ).fetchone()[0]

    f2 = _fact(subject="other", doc_id="d2")
    store.save_new(new_doc=("d2", "doc2.pdf"), new_facts={f2.fact_id: f2},
                    new_extra_evidence={}, new_relations=[])
    with sqlite3.connect(db_path) as conn:
        rowid_after = conn.execute(
            "SELECT rowid FROM facts WHERE fact_id = ?", (f1.fact_id,)
        ).fetchone()[0]
    assert rowid_before == rowid_after


# --------------------------------------------------------------------------
# 4. Duplicate document (via Store.ingest()'s own already-ingested guard —
#    unchanged by this backend; confirms SQLite doesn't interfere with it)
# --------------------------------------------------------------------------

def test_duplicate_document_is_a_noop_via_save_incremental(db_path, monkeypatch):
    from fact_layer.store import IngestResult
    store = Store(backend=SQLiteFactStore(db_path))
    skipped = IngestResult(doc_id="d1", filename="doc1.pdf", skipped_reason="already ingested")
    # Must not raise, and must not touch storage at all.
    store.save_incremental(skipped, store.backend)
    assert store.backend.load().facts == {}


# --------------------------------------------------------------------------
# 5 & 6. Duplicate fact / relation upsert semantics, relation persistence
# --------------------------------------------------------------------------

def test_resaving_the_same_full_snapshot_does_not_duplicate_rows(db_path):
    store = SQLiteFactStore(db_path)
    f1, f2 = _fact(val="100"), _fact(subject="other", measure="ebitda", val="50", doc_id="d1", char_start=10)
    rel = Relation(f1.fact_id, f2.fact_id, RelationType.CORROBORATES, 0.9, "value_match", "same", {})
    snap = _snapshot_with(f1, f2, relations=[rel])

    store.save(snap)
    store.save(snap)   # identical snapshot, saved twice

    loaded = store.load()
    assert len(loaded.facts) == 2
    assert len(loaded.relations) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 1


def test_relation_round_trips_with_full_fidelity(db_path):
    store = SQLiteFactStore(db_path)
    f1, f2 = _fact(val="100"), _fact(subject="other", val="105", char_start=10)
    rel = Relation(f1.fact_id, f2.fact_id, RelationType.CONTRADICTS, 0.73, "value_mismatch",
                   "The figures differ by 5%.", {"period": ("FY23", "FY24")}, decided_by="rule")
    store.save(_snapshot_with(f1, f2, relations=[rel]))

    loaded_rel = store.load().relations[0]
    assert loaded_rel.source_fact_id == f1.fact_id
    assert loaded_rel.target_fact_id == f2.fact_id
    assert loaded_rel.relation == RelationType.CONTRADICTS
    assert loaded_rel.confidence == 0.73
    assert loaded_rel.reason_code == "value_mismatch"
    assert loaded_rel.qualifier_diff == {"period": ["FY23", "FY24"]}   # JSON round-trip: tuple -> list
    assert loaded_rel.decided_by == "rule"


# --------------------------------------------------------------------------
# 7 & 8. Rejected-fact and resolution persistence
# --------------------------------------------------------------------------

def test_rejected_facts_round_trip(db_path):
    store = SQLiteFactStore(db_path)
    records = [
        {"raw_fact": {"subject_raw": "X", "measure_raw": None, "value_raw": "5"},
         "page_no": 3, "doc_id": "d1", "doc_filename": "doc1.pdf",
         "reason": "no_measure", "detail": ""},
        {"raw_fact": {"subject_raw": "Y", "measure_raw": "Z", "value_raw": "9"},
         "page_no": 7, "doc_id": "d2", "doc_filename": "doc2.pdf",
         "reason": "quote_not_found", "detail": "fuzzy ratio too low"},
    ]
    store.write_rejected_facts(records)
    loaded = store.list_rejected_facts()
    assert len(loaded) == 2
    assert loaded[0]["reason"] == "no_measure"
    assert loaded[1]["raw_fact"]["subject_raw"] == "Y"


def test_resolution_summary_round_trip(db_path):
    store = SQLiteFactStore(db_path)
    log = {
        "summary": {
            "total_decisions": 3, "llm_calls_used": 1,
            "canonical_measures": ["revenue", "ebitda"],
        },
        "decisions": [
            {"kind": "subject", "raw": "Acme", "canonical": "acme", "tier": "deterministic", "confidence": 1.0, "detail": ""},
            {"kind": "measure", "raw": "Revenue", "canonical": "revenue", "tier": "new", "confidence": 1.0, "detail": ""},
            {"kind": "measure", "raw": "revenue", "canonical": "revenue", "tier": "exact_repeat", "confidence": 1.0, "detail": ""},
        ],
    }
    store.write_resolution_summary(log)
    summary = store.read_resolution_summary()
    assert summary["total_decisions"] == 3
    assert summary["by_tier"] == {"deterministic": 1, "new": 1, "exact_repeat": 1}
    assert summary["llm_calls_used"] == 1
    assert summary["canonical_measures"] == ["revenue", "ebitda"]


# --------------------------------------------------------------------------
# 9. Rollback on failure
# --------------------------------------------------------------------------

def test_failed_incremental_write_rolls_back_and_preserves_prior_state(db_path):
    store = SQLiteFactStore(db_path)
    f1 = _fact(doc_id="d1")
    store.save_new(new_doc=("d1", "doc1.pdf"), new_facts={f1.fact_id: f1},
                    new_extra_evidence={}, new_relations=[])

    bad_relation = Relation("does_not_exist", f1.fact_id, RelationType.CORROBORATES, 0.5, "x", "x", {})
    with pytest.raises(sqlite3.IntegrityError):
        store.save_new(new_doc=None, new_facts={}, new_extra_evidence={}, new_relations=[bad_relation])

    snap = store.load()
    assert len(snap.facts) == 1 and f1.fact_id in snap.facts
    assert snap.relations == []


def test_failed_full_save_rolls_back_and_preserves_prior_state(db_path):
    store = SQLiteFactStore(db_path)
    f1 = _fact(doc_id="d1")
    store.save(_snapshot_with(f1))

    f2 = _fact(subject="other", doc_id="d2", char_start=20)
    bad_relation = Relation("does_not_exist", f2.fact_id, RelationType.CORROBORATES, 0.5, "x", "x", {})
    bad_snapshot = _snapshot_with(f1, f2, relations=[bad_relation], docs={"d1": "doc1.pdf", "d2": "doc2.pdf"})
    with pytest.raises(sqlite3.IntegrityError):
        store.save(bad_snapshot)

    # f2 must not have landed either — the whole transaction rolled back,
    # not just the relation insert.
    snap = store.load()
    assert set(snap.facts.keys()) == {f1.fact_id}
    assert snap.relations == []


# --------------------------------------------------------------------------
# 10. Reopening the database
# --------------------------------------------------------------------------

def test_reopening_the_database_preserves_all_data(db_path):
    store1 = SQLiteFactStore(db_path)
    f1, f2 = _fact(val="100"), _fact(subject="other", val="50", char_start=10)
    rel = Relation(f1.fact_id, f2.fact_id, RelationType.CORROBORATES, 0.9, "value_match", "same", {})
    store1.save(_snapshot_with(f1, f2, relations=[rel]))
    del store1

    store2 = SQLiteFactStore(db_path)   # fresh instance, same file
    snap = store2.load()
    assert len(snap.facts) == 2
    assert len(snap.relations) == 1
    assert snap.facts[f1.fact_id].subject == "acme"


# --------------------------------------------------------------------------
# 11. Foreign-key integrity
# --------------------------------------------------------------------------

def test_foreign_keys_are_enforced(db_path):
    store = SQLiteFactStore(db_path)
    with sqlite3.connect(db_path) as conn:
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()
    # PRAGMA foreign_keys is connection-scoped in SQLite, not persisted in
    # the file — assert this store's OWN connections turn it on, not that a
    # fresh ad-hoc connection inherits it.
    with store._connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    f1 = _fact(doc_id="d1")
    store.save(_snapshot_with(f1))
    orphan_relation = Relation(f1.fact_id, "no_such_fact", RelationType.CORROBORATES, 0.5, "x", "x", {})
    with pytest.raises(sqlite3.IntegrityError):
        store.save_new(new_doc=None, new_facts={}, new_extra_evidence={}, new_relations=[orphan_relation])


def test_deleting_a_fact_cascades_to_its_relations_and_evidence(db_path):
    """Not exercised through the FactStore contract (nothing deletes a fact
    today) — this pins the ON DELETE CASCADE schema choice itself, so a
    future caller that does delete a fact can rely on it leaving no orphan
    rows, matching the 'no orphan evidence / no orphan relations' invariant
    this phase asks for."""
    store = SQLiteFactStore(db_path)
    f1, f2 = _fact(val="100"), _fact(subject="other", val="50", char_start=10)
    rel = Relation(f1.fact_id, f2.fact_id, RelationType.CORROBORATES, 0.9, "value_match", "same", {})
    store.save(_snapshot_with(f1, f2, relations=[rel]))
    store.save_new(new_doc=None, new_facts={}, new_extra_evidence={f1.fact_id: [f1.evidence]}, new_relations=[])

    with store._connect() as conn:
        conn.execute("DELETE FROM facts WHERE fact_id = ?", (f1.fact_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extra_evidence WHERE fact_id = ?", (f1.fact_id,)).fetchone()[0] == 0


# --------------------------------------------------------------------------
# 12. Concurrent reads (WAL mode)
# --------------------------------------------------------------------------

def test_wal_mode_lets_a_reader_proceed_during_an_open_write_transaction(db_path):
    store = SQLiteFactStore(db_path)
    f1 = _fact(doc_id="d1")
    store.save(_snapshot_with(f1))

    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA journal_mode = WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE facts SET confidence = 0.5 WHERE fact_id = ?", (f1.fact_id,)
    )
    try:
        # A concurrent reader must see the last COMMITTED state, without
        # blocking, while the writer's transaction is still open — this is
        # exactly what WAL buys over the default rollback-journal mode.
        reader_snap = store.load()
        assert len(reader_snap.facts) == 1
    finally:
        writer.rollback()
        writer.close()


# --------------------------------------------------------------------------
# 13. Corrupted / missing database handling
# --------------------------------------------------------------------------

def test_missing_database_file_is_created_fresh_not_an_error(tmp_path):
    db_path = str(tmp_path / "nested" / "does_not_exist_yet.sqlite3")
    store = SQLiteFactStore(db_path)   # must not raise
    assert os.path.exists(db_path)
    assert store.load().facts == {}


def test_corrupted_database_file_raises_rather_than_silently_returning_empty(tmp_path):
    db_path = str(tmp_path / "corrupt.sqlite3")
    with open(db_path, "wb") as f:
        f.write(b"this is not a sqlite database file, just garbage bytes")
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteFactStore(db_path)


# --------------------------------------------------------------------------
# 14. JSON -> SQLite migration parity (small, isolated, synthetic corpus —
# never touches the real data/ files; see scripts/migrate_json_to_sqlite.py)
# --------------------------------------------------------------------------

def test_migration_script_parity_on_isolated_synthetic_corpus(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.migrate_json_to_sqlite import migrate, check_parity

    json_path = str(tmp_path / "store.json")
    rejected_path = str(tmp_path / "rejected_facts.jsonl")
    resolution_path = str(tmp_path / "resolution_log.json")
    sqlite_path = str(tmp_path / "store.sqlite3")

    f1, f2, f3 = (
        _fact(val="100"),
        _fact(subject="other", measure="ebitda", val="50", doc_id="d1", char_start=20),
        _fact(subject="acme", measure="revenue", val="105", doc_id="d2", char_start=0,
              period_label="FY2022-23"),
    )
    rel = Relation(f1.fact_id, f3.fact_id, RelationType.CONTRADICTS, 0.7, "value_mismatch", "differ", {})
    json_backend = JsonFactStore(store_path=json_path)
    json_backend.save(_snapshot_with(
        f1, f2, f3, relations=[rel], docs={"d1": "doc1.pdf", "d2": "doc2.pdf"},
    ))
    with open(rejected_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "raw_fact": {"subject_raw": "Q"}, "page_no": 1, "doc_id": "d1",
            "doc_filename": "doc1.pdf", "reason": "no_measure", "detail": "",
        }) + "\n")
    with open(resolution_path, "w", encoding="utf-8") as fh:
        json.dump({
            "summary": {"total_decisions": 1, "by_tier": {"new": 1}, "llm_calls_used": 0,
                       "canonical_measures": ["revenue"]},
            "decisions": [{"kind": "subject", "raw": "Acme", "canonical": "acme", "tier": "new",
                           "confidence": 1.0, "detail": ""}],
        }, fh)

    json_store, sqlite_store = migrate(json_path, rejected_path, resolution_path, sqlite_path)
    check_parity(json_store, sqlite_store)   # raises SystemExit on mismatch

    assert len(sqlite_store.facts) == 3
    assert len(sqlite_store.relations) == 1
    assert len(sqlite_store.backend.list_rejected_facts()) == 1
    assert sqlite_store.backend.read_resolution_summary()["total_decisions"] == 1


def test_migration_refuses_to_overwrite_an_existing_database(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.migrate_json_to_sqlite import migrate

    json_path = str(tmp_path / "store.json")
    JsonFactStore(store_path=json_path).save(_snapshot_with(_fact()))
    sqlite_path = str(tmp_path / "store.sqlite3")
    SQLiteFactStore(sqlite_path)   # pre-create it

    with pytest.raises(SystemExit):
        migrate(json_path, str(tmp_path / "no_rejected.jsonl"), str(tmp_path / "no_log.json"), sqlite_path)


# --------------------------------------------------------------------------
# Data parity: JSON and SQLite represent the SAME knowledge from the SAME
# source facts (Phase 13) — compare semantics, not incidental storage
# metadata (row ids, insertion order, timestamps).
# --------------------------------------------------------------------------

def test_json_and_sqlite_produce_semantically_identical_snapshots(tmp_path):
    f1 = _fact(subject="acme", measure="revenue", val="100", doc_id="d1", scope=Scope.STANDALONE)
    f2 = _fact(subject="acme", measure="revenue", val="105", doc_id="d2", char_start=0,
               period_label="FY2022-23", scope=Scope.CONSOLIDATED)
    rel = Relation(f1.fact_id, f2.fact_id, RelationType.APPARENT_CONFLICT, 0.85,
                   "scope_mismatch", "different bases", {"scope": ["standalone", "consolidated"]})
    snap = _snapshot_with(f1, f2, relations=[rel], docs={"d1": "doc1.pdf", "d2": "doc2.pdf"})

    json_backend = JsonFactStore(store_path=str(tmp_path / "s.json"))
    sqlite_backend = SQLiteFactStore(str(tmp_path / "s.sqlite3"))
    json_backend.save(snap)
    sqlite_backend.save(snap)

    js, ss = Store.load(json_backend), Store.load(sqlite_backend)
    assert js.canonical_summary() == ss.canonical_summary()
    assert set(js.facts.keys()) == set(ss.facts.keys())
    for fid in js.facts:
        assert js.facts[fid].to_dict() == ss.facts[fid].to_dict()
    assert js.ingested_docs == ss.ingested_docs
    assert {(r.source_fact_id, r.target_fact_id, r.relation) for r in js.relations} == \
           {(r.source_fact_id, r.target_fact_id, r.relation) for r in ss.relations}
