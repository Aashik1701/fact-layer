"""
Contract tests for fact_layer/storage.py (JsonFactStore, StoreSnapshot,
backend_from_env). These are fast and synthetic — no LLM, no real corpus —
because they test the persistence CONTRACT itself, not the pipeline that
produces facts. A future PostgresFactStore should be able to pass the same
assertions in this file against its own backend fixture.

Real-corpus/round-trip-through-Store coverage already exists in
test_store.py's test_full_corpus_store_saves_and_reloads and is not
duplicated here.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from fact_layer.models import Evidence, Fact, Qualifiers, Relation, RelationType, ValueKind
from fact_layer.normalize import parse_period, parse_quantity
from fact_layer.sqlite_storage import SQLiteFactStore
from fact_layer.storage import FactStore, JsonFactStore, StoreSnapshot, backend_from_env


def _mkfact(subject, measure, value_raw, doc_id="d1", page=1, period_label=None) -> Fact:
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=parse_quantity(value_raw),
        qualifiers=Qualifiers(period=parse_period(period_label) if period_label else None),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject, measure_raw=measure,
    )


@pytest.fixture
def backend(tmp_path) -> JsonFactStore:
    return JsonFactStore(
        store_path=str(tmp_path / "store.json"),
        rejected_path=str(tmp_path / "rejected_facts.jsonl"),
        resolution_log_path=str(tmp_path / "resolution_log.json"),
    )


# --------------------------------------------------------------------------
# save/load — the fact/relation/document graph
# --------------------------------------------------------------------------

def test_load_of_nonexistent_store_returns_empty_snapshot(backend):
    snapshot = backend.load()
    assert snapshot == StoreSnapshot()


def test_save_then_load_round_trips_a_fact(backend):
    f = _mkfact("acme", "revenue", "100000000", period_label="FY2023-24")
    backend.save(StoreSnapshot(facts={f.fact_id: f}))

    reloaded = backend.load()
    assert list(reloaded.facts.keys()) == [f.fact_id]
    got = reloaded.facts[f.fact_id]
    assert got.subject == f.subject and got.measure == f.measure
    assert got.value == f.value
    assert got.qualifiers == f.qualifiers
    assert got.evidence.doc_id == f.evidence.doc_id


def test_save_then_load_round_trips_extra_evidence(backend):
    """extra_evidence is exactly what dedupe_within_document() produces when
    it merges same-fact spans — a merged fact must not silently lose the
    spans it was merged from."""
    f = _mkfact("acme", "revenue", "100000000")
    extra_span = Evidence(doc_id="d1", page=45, char_start=0, char_end=9,
                          verbatim_quote="100000000", verified=True)
    backend.save(StoreSnapshot(facts={f.fact_id: f}, extra_evidence={f.fact_id: [extra_span]}))

    reloaded = backend.load()
    assert len(reloaded.extra_evidence[f.fact_id]) == 1
    assert reloaded.extra_evidence[f.fact_id][0].page == 45


def test_save_then_load_round_trips_a_relation(backend):
    f1 = _mkfact("acme", "revenue", "100000000", doc_id="d1")
    f2 = _mkfact("acme", "revenue", "100000000", doc_id="d2")
    rel = Relation(
        source_fact_id=f1.fact_id, target_fact_id=f2.fact_id,
        relation=RelationType.CORROBORATES, confidence=0.9,
        reason_code="same_value", explanation="both documents report the same figure",
        qualifier_diff={"period": "same"}, decided_by="rule",
    )
    backend.save(StoreSnapshot(
        facts={f1.fact_id: f1, f2.fact_id: f2}, relations=[rel],
    ))

    reloaded = backend.load()
    assert len(reloaded.relations) == 1
    got = reloaded.relations[0]
    assert got.source_fact_id == f1.fact_id
    assert got.relation == RelationType.CORROBORATES
    assert got.qualifier_diff == {"period": "same"}


def test_save_then_load_round_trips_clusters_and_documents(backend):
    snapshot = StoreSnapshot(
        clusters={"acme::revenue": ["fact-1", "fact-2"]},
        ingested_docs={"doc-hash-1": "report.pdf"},
    )
    backend.save(snapshot)
    reloaded = backend.load()
    assert reloaded.clusters == {"acme::revenue": ["fact-1", "fact-2"]}
    assert reloaded.ingested_docs == {"doc-hash-1": "report.pdf"}


def test_list_facts_reflects_multiple_saved_facts(backend):
    facts = {f.fact_id: f for f in [
        _mkfact("acme", "revenue", "100000000", doc_id="d1"),
        _mkfact("acme", "net_income", "5000000", doc_id="d1"),
    ]}
    backend.save(StoreSnapshot(facts=facts))
    reloaded = backend.load()
    assert len(reloaded.facts) == 2


def test_save_is_atomic_no_temp_file_left_behind(backend):
    f = _mkfact("acme", "revenue", "100000000")
    backend.save(StoreSnapshot(facts={f.fact_id: f}))
    assert os.path.exists(backend.store_path)
    assert not os.path.exists(f"{backend.store_path}.tmp")


def test_save_overwrites_prior_snapshot_entirely(backend):
    f1 = _mkfact("acme", "revenue", "100000000")
    backend.save(StoreSnapshot(facts={f1.fact_id: f1}))
    f2 = _mkfact("beta", "net_income", "5000000")
    backend.save(StoreSnapshot(facts={f2.fact_id: f2}))

    reloaded = backend.load()
    assert list(reloaded.facts.keys()) == [f2.fact_id], \
        "save() replaces the whole snapshot — it is not an incremental merge"


# --------------------------------------------------------------------------
# rejected-facts (read side) — api.py's /stats and /rejected-facts
# --------------------------------------------------------------------------

def test_list_rejected_facts_empty_when_file_absent(backend):
    assert backend.list_rejected_facts() == []


def test_list_rejected_facts_reads_back_appended_rows(backend):
    os.makedirs(os.path.dirname(backend.rejected_path), exist_ok=True)
    with open(backend.rejected_path, "a", encoding="utf-8") as fh:
        fh.write('{"reason": "no_subject", "raw_fact": {}, "page_no": 1, '
                 '"doc_id": "d1", "doc_filename": "a.pdf", "detail": ""}\n')
        fh.write('{"reason": "span_mismatch", "raw_fact": {}, "page_no": 2, '
                 '"doc_id": "d1", "doc_filename": "a.pdf", "detail": "x"}\n')

    rows = backend.list_rejected_facts()
    assert len(rows) == 2
    assert {r["reason"] for r in rows} == {"no_subject", "span_mismatch"}


def test_list_rejected_facts_skips_malformed_lines_without_raising(backend):
    os.makedirs(os.path.dirname(backend.rejected_path), exist_ok=True)
    with open(backend.rejected_path, "a", encoding="utf-8") as fh:
        fh.write('{"reason": "no_subject"}\n')
        fh.write("not json at all\n")
        fh.write("\n")   # blank line
        fh.write('{"reason": "no_measure"}\n')

    rows = backend.list_rejected_facts()
    assert len(rows) == 2
    assert {r["reason"] for r in rows} == {"no_subject", "no_measure"}


# --------------------------------------------------------------------------
# resolution summary (read side) — api.py's /stats
# --------------------------------------------------------------------------

def test_read_resolution_summary_none_when_file_absent(backend):
    assert backend.read_resolution_summary() is None


def test_read_resolution_summary_returns_summary_key(backend):
    import json
    os.makedirs(os.path.dirname(backend.resolution_log_path), exist_ok=True)
    with open(backend.resolution_log_path, "w", encoding="utf-8") as fh:
        json.dump({"summary": {"total_decisions": 7, "by_tier": {"exact": 5}}, "decisions": []}, fh)

    summary = backend.read_resolution_summary()
    assert summary == {"total_decisions": 7, "by_tier": {"exact": 5}}


# --------------------------------------------------------------------------
# backend_from_env — the STORAGE_BACKEND configuration seam
# --------------------------------------------------------------------------

def test_backend_from_env_defaults_to_json(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    backend = backend_from_env(store_path=str(tmp_path / "s.json"))
    assert isinstance(backend, JsonFactStore)


def test_backend_from_env_explicit_json(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "json")
    backend = backend_from_env(store_path=str(tmp_path / "s.json"))
    assert isinstance(backend, JsonFactStore)


def test_backend_from_env_postgres_raises_not_implemented(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    with pytest.raises(NotImplementedError):
        backend_from_env()


def test_backend_from_env_explicit_sqlite(monkeypatch, tmp_path):
    """"sqlite" used to be this module's example of an UNKNOWN backend value
    (see test_backend_from_env_unknown_value_raises_value_error below,
    which used to set this exact env var and expect ValueError) — now that
    SQLiteFactStore is a real, working backend, that assumption is wrong;
    this test replaces it as the positive case, and the ValueError test
    below was updated to use a genuinely unsupported name instead."""
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "s.sqlite3"))
    backend = backend_from_env(store_path=str(tmp_path / "unused.json"))
    assert isinstance(backend, SQLiteFactStore)
    assert backend.db_path == str(tmp_path / "s.sqlite3")


def test_backend_from_env_sqlite_default_path_when_unset(monkeypatch, tmp_path):
    """Verifies the fallback itself (SQLITE_PATH unset -> the module's
    default constant), without ever letting a test actually create a file
    at the real default path (data/store.sqlite3) — sqlite_storage's
    _DEFAULT_SQLITE_PATH is monkeypatched to an isolated tmp_path first, and
    backend_from_env()'s lazy `from .sqlite_storage import
    ..._DEFAULT_SQLITE_PATH` picks up that patched value at call time."""
    import fact_layer.sqlite_storage as sqlite_storage_module
    isolated_default = str(tmp_path / "default.sqlite3")
    monkeypatch.setattr(sqlite_storage_module, "_DEFAULT_SQLITE_PATH", isolated_default)
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("SQLITE_PATH", raising=False)

    backend = backend_from_env()
    assert isinstance(backend, SQLiteFactStore)
    assert backend.db_path == isolated_default


def test_backend_from_env_unknown_value_raises_value_error(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "mongodb")
    with pytest.raises(ValueError):
        backend_from_env()


# --------------------------------------------------------------------------
# FactStore is a real abstract contract, not just a JsonFactStore alias
# --------------------------------------------------------------------------

def test_fact_store_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        FactStore()


def test_json_fact_store_is_a_fact_store(backend):
    assert isinstance(backend, FactStore)
