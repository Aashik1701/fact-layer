"""
Evidence Lineage (fact_layer/lineage.py) — domain tests.

Lineage is built entirely from fact_layer.graph.GraphProjection, so the
tests that matter most are integrity tests: every node/edge lineage
returns must be something GraphProjection itself would produce, nothing is
invented, nothing mutates the store, and the same inputs always produce
the same output.
"""

import hashlib
import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.graph import GraphProjection, NODE_DOCUMENT, NODE_EVIDENCE, NODE_FACT
from fact_layer.lineage import fact_lineage, relation_lineage
from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Relation,
    RelationType, Scope, ValueKind,
)
from fact_layer.store import Store, _STORE_PATH


def _mkfact(subject="acme", measure="revenue", val="100", period="FY2023-24",
            doc_id="docA", page=1, scope=Scope.UNKNOWN, fact_id_suffix="") -> Fact:
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(val), unit="currency", currency="INR",
                       sig_figs=4, raw=val),
        qualifiers=Qualifiers(
            period=Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label=period),
            scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(val),
                          verbatim_quote=f"{measure} {val}{fact_id_suffix}", verified=True),
        subject_raw=subject, measure_raw=measure, value_verification="verified",
    )


def _relation_id(rel: Relation) -> str:
    seed = f"{rel.source_fact_id}|{rel.target_fact_id}|{rel.relation.value}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


@pytest.fixture()
def small_store():
    store = Store()
    a = _mkfact(val="120", doc_id="docA", page=7)
    b = _mkfact(val="145", doc_id="docB", page=18, scope=Scope.CONSOLIDATED, fact_id_suffix="_b")
    unrelated = _mkfact(subject="other_co", measure="ebitda", val="55", doc_id="docA", page=9, fact_id_suffix="_u")
    for f in (a, b, unrelated):
        store.facts[f.fact_id] = f
    store.ingested_docs = {"docA": "annual-report.pdf", "docB": "investor-deck.pdf"}
    rel = Relation(a.fact_id, b.fact_id, RelationType.APPARENT_CONFLICT, 0.85,
                   "scope_mismatch", "Reported on different bases.", {})
    store.relations = [rel]
    return store, a, b, unrelated, rel


@pytest.fixture(scope="module")
def real_store():
    return Store.load(_STORE_PATH)


# --------------------------------------------------------------------------
# fact -> evidence -> document
# --------------------------------------------------------------------------

def test_fact_lineage_includes_own_evidence_and_document(small_store):
    store, a, _b, _u, _rel = small_store
    result = fact_lineage(store, a.fact_id)
    assert result is not None
    doc_ids = {n["metadata"]["doc_id"] for n in result.evidence}
    assert "docA" in doc_ids
    filenames = {n["label"] for n in result.documents}
    assert "annual-report.pdf" in filenames


def test_fact_lineage_evidence_matches_get_evidence_exactly(small_store):
    store, a, _b, _u, _rel = small_store
    result = fact_lineage(store, a.fact_id)
    expected_quotes = {e.verbatim_quote for e in store.get_evidence(a.fact_id)}
    actual_quotes = {n["metadata"]["verbatim_quote"] for n in result.evidence}
    assert actual_quotes == expected_quotes


def test_fact_lineage_unknown_id_returns_none(small_store):
    store, *_ = small_store
    assert fact_lineage(store, "not_a_real_fact_id") is None


# --------------------------------------------------------------------------
# relation -> both facts -> their evidence -> documents
# --------------------------------------------------------------------------

def test_relation_lineage_includes_both_facts(small_store):
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    result = relation_lineage(store, rel, rid)
    fact_ids = {n["source_id"] for n in result.facts}
    assert a.fact_id in fact_ids
    assert b.fact_id in fact_ids


def test_relation_lineage_excludes_unrelated_facts(small_store):
    store, a, b, unrelated, rel = small_store
    rid = _relation_id(rel)
    result = relation_lineage(store, rel, rid)
    fact_ids = {n["source_id"] for n in result.facts}
    assert unrelated.fact_id not in fact_ids


def test_relation_lineage_root_conclusion_matches_relation_verbatim(small_store):
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    result = relation_lineage(store, rel, rid)
    root = result.root_conclusion
    assert root.kind == "relation"
    assert root.relation_id == rid
    assert root.relation == rel.relation.value
    assert root.confidence == rel.confidence
    assert root.reason_code == rel.reason_code
    assert root.explanation == rel.explanation
    assert root.source_fact_id == a.fact_id
    assert root.target_fact_id == b.fact_id


def test_relation_lineage_includes_connecting_relation_edge(small_store):
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    result = relation_lineage(store, rel, rid)
    matching = [e for e in result.edges if e["type"] == rel.relation.value.upper()]
    assert len(matching) == 1
    assert matching[0]["metadata"]["relation_id"] == rid


# --------------------------------------------------------------------------
# Integrity: every node/edge lineage returns is something GraphProjection
# itself would independently produce — this is what "reuse the graph, don't
# build a second one" actually pins.
# --------------------------------------------------------------------------

def test_relation_lineage_nodes_are_subset_of_graph_projection(small_store):
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    result = relation_lineage(store, rel, rid)

    projection = GraphProjection(store)
    independent_ids: set[str] = set()
    for fid in (a.fact_id, b.fact_id):
        sub = projection.neighborhood(NODE_FACT, fid, depth=1)
        independent_ids.update(n["id"] for n in sub["nodes"])

    lineage_ids = {n["id"] for n in result.nodes}
    assert lineage_ids.issubset(independent_ids)


def test_fact_lineage_matches_direct_graph_neighborhood(small_store):
    store, a, _b, _u, _rel = small_store
    result = fact_lineage(store, a.fact_id)
    direct = GraphProjection(store).neighborhood(NODE_FACT, a.fact_id, depth=1)
    assert {n["id"] for n in result.nodes} == {n["id"] for n in direct["nodes"]}
    assert {e["id"] for e in result.edges} == {e["id"] for e in direct["edges"]}


# --------------------------------------------------------------------------
# Missing evidence / document metadata — degrade, never invent
# --------------------------------------------------------------------------

def test_fact_with_no_evidence_still_returns_lineage_without_fabricating_any():
    store = Store()
    f = Fact(
        subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal("100"), unit="currency", currency="INR", sig_figs=3, raw="100"),
        qualifiers=Qualifiers(), evidence=None,
        subject_raw="acme", measure_raw="revenue",
    )
    store.facts[f.fact_id] = f
    result = fact_lineage(store, f.fact_id)
    assert result is not None
    assert result.evidence == []
    assert result.documents == []


# --------------------------------------------------------------------------
# No mutation
# --------------------------------------------------------------------------

def test_lineage_calls_do_not_mutate_store(small_store):
    import copy
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    facts_before = copy.deepcopy(store.facts)
    relations_before = copy.deepcopy(store.relations)

    fact_lineage(store, a.fact_id)
    relation_lineage(store, rel, rid)

    assert store.facts == facts_before
    assert store.relations == relations_before


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_relation_lineage_deterministic(small_store):
    store, a, b, _u, rel = small_store
    rid = _relation_id(rel)
    first = relation_lineage(store, rel, rid).to_dict()
    second = relation_lineage(store, rel, rid).to_dict()
    assert first == second


# --------------------------------------------------------------------------
# Real corpus
# --------------------------------------------------------------------------

def test_real_corpus_relation_lineage(real_store):
    rel = real_store.relations[0]
    rid = _relation_id(rel)
    result = relation_lineage(real_store, rel, rid)
    fact_ids = {n["source_id"] for n in result.facts}
    assert rel.source_fact_id in fact_ids
    assert rel.target_fact_id in fact_ids
    assert result.root_conclusion.explanation == rel.explanation


def test_real_corpus_fact_lineage_for_every_relation_endpoint(real_store):
    """Every fact that participates in a real relation must have a
    resolvable, non-empty lineage."""
    checked = 0
    for rel in real_store.relations[:5]:
        for fid in (rel.source_fact_id, rel.target_fact_id):
            result = fact_lineage(real_store, fid)
            assert result is not None
            checked += 1
    assert checked > 0


def test_real_corpus_no_mutation(real_store):
    import copy
    facts_before = copy.deepcopy(real_store.facts)
    relations_before = copy.deepcopy(real_store.relations)
    for rel in real_store.relations[:3]:
        rid = _relation_id(rel)
        relation_lineage(real_store, rel, rid)
        fact_lineage(real_store, rel.source_fact_id)
    assert real_store.facts == facts_before
    assert real_store.relations == relations_before
