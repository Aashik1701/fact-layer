"""Tests for fact_layer/store.py (milestone 4).

Dedup and incremental-relations tests use fast synthetic facts. The corpus-
level tests (cluster/relation counts, save/load round-trip, and the "7th
document" incrementality proof) run the real pipeline over all 6 starter
PDFs in LLM_MODE=replay against the committed cache/llm/ — slow (one real
extraction pass) but this is what CLAUDE.md section 14's exit criterion and
the store-level exit criterion both require: real data, not stand-ins.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from fact_layer.models import Evidence, Fact, Qualifiers, RelationType, ValueKind
from fact_layer.normalize import parse_period, parse_quantity
from fact_layer.store import (
    Store,
    _drop_same_document_corroboration,
    _incremental_pairwise_relations,
    dedupe_within_document,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Real-corpus tests below ingest all 6 starter PDFs (sometimes twice, across
# different test functions) via Store.ingest(), which by default logs
# span-verification rejections to the real data/rejected_facts.jsonl — a
# graded deliverable (CLAUDE.md section 3.1). Without redirecting it, every
# `pytest -q` run silently appends another full corpus's worth of rejections
# on top of whatever is already there. This path is outside the repo and
# shared by every real-corpus ingest call in this file.
_TEST_REJECTED_PATH = os.path.join(tempfile.mkdtemp(prefix="fact_layer_test_"), "rejected_facts.jsonl")


def _no_network(*args, **kwargs):
    raise AssertionError("no network call should be attempted — replay/committed cache only")


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    yield


def _mkfact(subject, measure, value_raw, doc_id="d1", page=1, period_label=None) -> Fact:
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=parse_quantity(value_raw),
        qualifiers=Qualifiers(period=parse_period(period_label) if period_label else None),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject, measure_raw=measure,
    )


# --------------------------------------------------------------------------
# Dedup (CLAUDE.md section 11)
# --------------------------------------------------------------------------

def test_dedup_collapses_same_document_repeats():
    f1 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=3)
    f2 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=45)
    deduped, extra = dedupe_within_document([f1, f2])
    assert len(deduped) == 1
    assert deduped[0].fact_id in extra
    assert len(extra[deduped[0].fact_id]) == 1


def test_dedup_does_not_collapse_across_documents():
    f1 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=3)
    f2 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d2", page=3)
    deduped, extra = dedupe_within_document([f1, f2])
    assert len(deduped) == 2
    assert extra == {}


def test_dedup_does_not_collapse_different_values():
    f1 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=3, period_label="FY2023-24")
    f2 = _mkfact("delhivery", "revenue", "3210000000", doc_id="d1", page=45, period_label="FY2024-25")
    deduped, extra = dedupe_within_document([f1, f2])
    assert len(deduped) == 2


def test_dedup_takes_max_confidence():
    f1 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=3)
    f1.confidence = 0.7
    f2 = _mkfact("delhivery", "revenue", "1204000000", doc_id="d1", page=45)
    f2.confidence = 0.95
    deduped, extra = dedupe_within_document([f1, f2])
    assert deduped[0].confidence == 0.95


# --------------------------------------------------------------------------
# Same-document corroboration weighted at zero (CLAUDE.md section 11)
# --------------------------------------------------------------------------

def test_same_document_corroboration_dropped_cross_document_kept():
    f1 = _mkfact("acme", "revenue", "100000000", doc_id="d1", page=1, period_label="FY2023-24")
    f2 = _mkfact("acme", "revenue", "100000000", doc_id="d1", page=99, period_label="FY2023-24")
    f3 = _mkfact("acme", "revenue", "100000000", doc_id="d2", page=1, period_label="FY2023-24")

    facts_by_id = {f1.fact_id: f1, f2.fact_id: f2, f3.fact_id: f3}
    relations = _incremental_pairwise_relations([f2, f3], [f1])
    assert any(r.relation == RelationType.CORROBORATES for r in relations)

    filtered = _drop_same_document_corroboration(relations, facts_by_id)
    same_doc_pair_present = any(
        {r.source_fact_id, r.target_fact_id} == {f1.fact_id, f2.fact_id} for r in filtered
    )
    assert not same_doc_pair_present, "same-document corroboration must be dropped"
    assert any(r.relation == RelationType.CORROBORATES for r in filtered), \
        "cross-document corroboration must survive the filter"


# --------------------------------------------------------------------------
# Incremental ingest: only new-touching pairs get computed
# --------------------------------------------------------------------------

def test_incremental_pairwise_relations_skips_old_vs_old():
    existing = [_mkfact("acme", "revenue", f"{100+i}0000000", doc_id=f"d{i}", page=1, period_label="FY2023-24")
                for i in range(10)]
    new_fact = _mkfact("acme", "revenue", "999000000", doc_id="d99", page=1, period_label="FY2023-24")

    relations = _incremental_pairwise_relations([new_fact], existing)
    new_id = new_fact.fact_id
    for r in relations:
        assert new_id in (r.source_fact_id, r.target_fact_id), \
            "every relation from an incremental add must touch the new fact"
    assert len(relations) <= len(existing)   # new-vs-each-existing, never old-vs-old too


def test_store_growing_cluster_adds_only_new_touching_relations():
    """Seed a cluster as if a prior ingest already fully computed it, then
    grow it by one fact via the same incremental path Store.ingest() uses —
    the number of relations added must scale with the existing cluster size
    (new-vs-each-existing), not its square."""
    from fact_layer.adjudicate import adjudicate_cluster

    store = Store()
    existing = [_mkfact("acme", "revenue", f"{100+i}0000000", doc_id=f"d{i}", page=1, period_label="FY2023-24")
                for i in range(20)]
    for f in existing:
        store.facts[f.fact_id] = f
        store.clusters.setdefault(f.cluster_key(), []).append(f.fact_id)
    store.relations.extend(adjudicate_cluster(existing))
    baseline = len(store.relations)

    new_fact = _mkfact("acme", "revenue", "999000000", doc_id="d999", page=1, period_label="FY2023-24")
    store.facts[new_fact.fact_id] = new_fact
    ck = new_fact.cluster_key()
    store.clusters[ck].append(new_fact.fact_id)

    new_ids = {new_fact.fact_id}
    fact_ids = store.clusters[ck]
    new_here = [store.facts[fid] for fid in fact_ids if fid in new_ids]
    existing_here = [store.facts[fid] for fid in fact_ids if fid not in new_ids]
    added = _incremental_pairwise_relations(new_here, existing_here)
    store.relations.extend(added)

    assert len(added) <= len(existing)
    assert len(store.relations) == baseline + len(added)


# --------------------------------------------------------------------------
# Real 6-doc corpus (replay mode, committed cache — slow, one-time per file)
# --------------------------------------------------------------------------

_full_store_cache: dict = {}


def _full_store() -> Store:
    if "store" not in _full_store_cache:
        import glob
        paths = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
        store = Store()
        for p in paths:
            store.ingest(p, rejected_path=_TEST_REJECTED_PATH)
        _full_store_cache["store"] = store
    return _full_store_cache["store"]


def test_full_corpus_cluster_and_relation_counts():
    store = _full_store()
    summary = store.canonical_summary()
    assert summary["total_facts"] > 0
    assert summary["clusters_with_2plus_facts"] >= 1
    assert summary["total_relations"] >= 1
    for rel_type in summary["relation_counts"]:
        assert rel_type in {r.value for r in RelationType}


def test_full_corpus_ingest_is_idempotent_on_repeat():
    store = _full_store()
    fact_count_before = len(store.facts)
    relation_count_before = len(store.relations)

    path = os.path.join(ROOT, "starter-datasets", "delhivery", "01-delhivery-prospectus-2022-excerpt.pdf")
    result = store.ingest(path)   # already ingested by _full_store()
    assert result.skipped_reason == "already ingested"
    assert len(store.facts) == fact_count_before
    assert len(store.relations) == relation_count_before


def test_get_evidence_returns_merged_spans_from_real_corpus():
    """A real deduplicated fact must expose MORE spans through
    Store.get_evidence() than `fact.evidence` alone shows — that gap is the
    entire reason get_evidence() is the sanctioned read path.

    Note on the real data: the two merged-evidence facts in this corpus both
    merge overlapping spans on the SAME page (the extractor quoted one figure
    twice, once with a wider surrounding sentence and once narrowly), not the
    cross-page repeat CLAUDE.md section 11 anticipates. Distinct char offsets
    on one page are still genuinely distinct evidence spans, and they exercise
    the merge path identically, so this asserts on span count and distinctness
    rather than on page-distinctness, which the corpus does not currently
    supply.
    """
    store = _full_store()

    merged_ids = [fid for fid in store.facts if store.extra_evidence.get(fid)]
    assert merged_ids, "expected at least one deduplicated fact with merged evidence"

    fid = merged_ids[0]
    fact = store.facts[fid]
    spans = store.get_evidence(fid)

    assert fact.evidence is not None
    assert len(spans) > 1, "get_evidence() must surface spans fact.evidence alone cannot"
    assert len(spans) == 1 + len(store.extra_evidence[fid])

    # the fact's own primary span comes first, and every span is distinct
    assert spans[0] == fact.evidence
    keys = {(e.doc_id, e.page, e.char_start, e.char_end) for e in spans}
    assert len(keys) == len(spans), "merged spans must be deduplicated, not repeated"


def test_get_evidence_deduplicates_identical_spans():
    """If the same span somehow lands in both fact.evidence and
    extra_evidence, get_evidence() must return it once, not twice."""
    store = Store()
    f = _mkfact("acme", "revenue", "100000000", doc_id="d1", page=1)
    store.facts[f.fact_id] = f
    store.extra_evidence[f.fact_id] = [f.evidence]   # exact duplicate of the primary
    spans = store.get_evidence(f.fact_id)
    assert len(spans) == 1


def test_get_evidence_unknown_fact_id_returns_empty():
    store = Store()
    assert store.get_evidence("f_does_not_exist") == []


def test_get_evidence_fact_without_merges_returns_single_span():
    store = Store()
    f = _mkfact("acme", "revenue", "100000000", doc_id="d1", page=1)
    store.facts[f.fact_id] = f
    spans = store.get_evidence(f.fact_id)
    assert len(spans) == 1
    assert spans[0] == f.evidence


def test_full_corpus_store_saves_and_reloads(tmp_path):
    store = _full_store()
    path = str(tmp_path / "store.json")
    store.save(path)
    reloaded = Store.load(path)
    assert len(reloaded.facts) == len(store.facts)
    assert len(reloaded.relations) == len(store.relations)
    assert reloaded.clusters == store.clusters
    # spot check one fact round-trips with the right type for its value
    sample_id = next(iter(store.facts))
    assert reloaded.facts[sample_id].value == store.facts[sample_id].value
    assert reloaded.facts[sample_id].qualifiers == store.facts[sample_id].qualifiers


def test_incremental_ingest_of_final_document_only_touches_new_clusters():
    """Proves ingest() is incremental, not O(n^2)-per-call, using the same
    'add one more document to an existing store' scenario CLAUDE.md's exit
    criterion describes. We only have 6 real starter PDFs (no literal 7th
    document exists in this repo to fabricate one from without either
    reusing an already-ingested doc_id — parse.py's parsed-document cache is
    content-hash keyed, so a byte-identical copy silently resolves to the
    SAME doc_id and gets skipped as already-ingested — or risking a PDF
    trailing-bytes trick, which turned out NOT to be safe: it changed the
    extracted text pdfplumber produced on at least one page, causing a real
    replay cache miss). Ingesting 5 of the 6 documents first and timing the
    6th's ingest into that existing store demonstrates exactly the same
    thing: adding one more document only touches the clusters it actually
    contributes to, and is fast because every LLM call it needs is already
    cached — the scenario doesn't depend on whether we call it the 6th or a
    hypothetical 7th."""
    import glob
    paths = sorted(glob.glob(os.path.join(ROOT, "starter-datasets", "**", "*.pdf"), recursive=True))
    assert len(paths) == 6

    store = Store()
    for p in paths[:-1]:
        store.ingest(p, rejected_path=_TEST_REJECTED_PATH)

    clusters_before = {ck: list(fids) for ck, fids in store.clusters.items()}
    relation_count_before = len(store.relations)

    from fact_layer import llm
    network_calls_before = llm._stats["network_calls"]

    t0 = time.time()
    result = store.ingest(paths[-1], rejected_path=_TEST_REJECTED_PATH)
    elapsed = time.time() - t0

    assert llm._stats["network_calls"] == network_calls_before, \
        "this ingest must be fully servable from the committed replay cache — no live calls"
    assert result.skipped_reason is None
    # Not a tight bound: this is genuine CPU-bound work (span verification,
    # resolve.py's fuzzy measure matching against an already-large canonical
    # registry, gate+adjudicate for every touched cluster) with zero network
    # calls, not a synthetic no-op — 150s comfortably separates "did real,
    # incremental work" from "silently fell back to a full O(n^2) recompute
    # across the whole store," which is what this assertion actually guards
    # against.
    assert elapsed < 150, f"a fully-replay-cached ingest took {elapsed:.1f}s — investigate for a full re-extraction"
    assert result.new_relations, "the largest, most cross-referenced document should touch existing clusters"

    untouched = [ck for ck in clusters_before if ck not in result.touched_clusters]
    assert untouched, "sanity check: most existing clusters should be untouched by one more document"
    for ck in untouched:
        assert store.clusters[ck] == clusters_before[ck], \
            f"cluster {ck!r} was not touched by this ingest but changed anyway"

    assert len(store.relations) == relation_count_before + len(result.new_relations)
