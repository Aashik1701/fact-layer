"""
Knowledge-graph projection — domain tests.

The graph is a PROJECTION, so the tests that matter most are the ones that
prove it cannot become a second source of truth: every relation edge must
exist in `Store.relations`, every evidence edge must belong to the fact it
hangs off, every document link must match the evidence's own `doc_id`, and
nothing may be invented, duplicated or merged.

Runs against the real committed store as well as small hand-built stores, so
a regression shows up both in the abstract and on the corpus that ships.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer import graph as g
from fact_layer.graph import (
    GraphProjection, NODE_DOCUMENT, NODE_ENTITY, NODE_EVIDENCE, NODE_FACT,
    STRUCTURAL_EDGE_TYPES, build_neighborhood,
)
from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Relation,
    RelationType, Scope, ValueKind,
)
from fact_layer.store import Store, _STORE_PATH


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _mkfact(subject="acme", measure="revenue", val="100", period="FY2023-24",
            doc_id="docA", page=1, scope=Scope.UNKNOWN) -> Fact:
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(val), unit="currency", currency="INR",
                       sig_figs=3, raw=val),
        qualifiers=Qualifiers(
            period=Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label=period),
            scope=scope),
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(val),
                          verbatim_quote=f"{measure} {val}", verified=True),
        subject_raw=subject, measure_raw=measure, value_verification="verified",
    )


@pytest.fixture()
def small_store():
    store = Store()
    a = _mkfact(val="100", doc_id="docA", page=7)
    b = _mkfact(val="101", doc_id="docB", page=18)
    c = _mkfact(subject="other_co", measure="ebitda", val="55", doc_id="docA", page=9)
    for f in (a, b, c):
        store.facts[f.fact_id] = f
    store.ingested_docs = {"docA": "annual-report.pdf", "docB": "investor-deck.pdf"}
    store.relations = [
        Relation(a.fact_id, b.fact_id, RelationType.CORROBORATES, 0.9,
                 "comparable", "Same subject, measure, scope, unit and period.", {}),
    ]
    return store, a, b, c


@pytest.fixture(scope="module")
def real_store():
    return Store.load(_STORE_PATH)


def _by_type(payload, node_type):
    return [n for n in payload["nodes"] if n["type"] == node_type]


def _edges_of(payload, edge_type):
    return [e for e in payload["edges"] if e["type"] == edge_type]


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_entity_fact_evidence_document_chain_exists(small_store):
    store, a, _b, _c = small_store
    payload = build_neighborhood(store, NODE_FACT, a.fact_id, depth=2)

    assert _by_type(payload, NODE_ENTITY)
    assert _by_type(payload, NODE_FACT)
    assert _by_type(payload, NODE_EVIDENCE)
    assert _by_type(payload, NODE_DOCUMENT)
    assert _edges_of(payload, g.EDGE_HAS_FACT)
    assert _edges_of(payload, g.EDGE_SUPPORTED_BY)
    assert _edges_of(payload, g.EDGE_LOCATED_IN)


def test_every_evidence_node_reaches_a_document(small_store):
    """A dangling evidence node cannot answer 'which document did this come
    from?', which is the one question the graph exists to answer."""
    store, a, _b, _c = small_store
    payload = build_neighborhood(store, NODE_FACT, a.fact_id, depth=2)
    evidence_ids = {n["id"] for n in _by_type(payload, NODE_EVIDENCE)}
    linked = {e["source"] for e in _edges_of(payload, g.EDGE_LOCATED_IN)}
    assert evidence_ids <= linked


def test_relation_edge_uses_the_adjudicators_own_metadata(small_store):
    store, a, b, _c = small_store
    payload = build_neighborhood(store, NODE_FACT, a.fact_id, depth=2)
    rel_edges = [e for e in payload["edges"] if e["type"] not in STRUCTURAL_EDGE_TYPES]
    assert len(rel_edges) == 1
    edge = rel_edges[0]
    assert edge["type"] == "CORROBORATES"
    assert edge["metadata"]["confidence"] == 0.9
    assert edge["metadata"]["reason_code"] == "comparable"
    assert edge["source"] == g.fact_node_id(a.fact_id)
    assert edge["target"] == g.fact_node_id(b.fact_id)


def test_no_node_or_edge_is_duplicated(small_store, real_store):
    for store, root_type, root_id in (
        (small_store[0], NODE_FACT, small_store[1].fact_id),
        (real_store, NODE_FACT, real_store.relations[0].source_fact_id),
    ):
        payload = build_neighborhood(store, root_type, root_id, depth=2)
        ids = [n["id"] for n in payload["nodes"]]
        eids = [e["id"] for e in payload["edges"]]
        assert len(ids) == len(set(ids))
        assert len(eids) == len(set(eids))


def test_projection_is_deterministic(real_store):
    import json
    root = real_store.relations[0].source_fact_id
    first = json.dumps(build_neighborhood(real_store, NODE_FACT, root, depth=2), sort_keys=True)
    for _ in range(3):
        assert json.dumps(
            build_neighborhood(real_store, NODE_FACT, root, depth=2), sort_keys=True) == first


# --------------------------------------------------------------------------
# The graph invents nothing (spec §3, §42)
# --------------------------------------------------------------------------

def test_every_relation_edge_exists_in_the_authoritative_store(real_store):
    authoritative = {
        (r.source_fact_id, r.target_fact_id, r.relation.value.upper())
        for r in real_store.relations
    }
    checked = 0
    for rel in real_store.relations[:10]:
        payload = build_neighborhood(real_store, NODE_FACT, rel.source_fact_id, depth=2)
        for edge in payload["edges"]:
            if edge["type"] in STRUCTURAL_EDGE_TYPES:
                continue
            triple = (edge["metadata"]["source_fact_id"],
                      edge["metadata"]["target_fact_id"], edge["type"])
            assert triple in authoritative, f"graph invented relation {triple}"
            checked += 1
    assert checked > 0


def test_every_evidence_edge_belongs_to_its_fact(real_store):
    proj = GraphProjection(real_store)
    for rel in real_store.relations[:5]:
        payload = proj.neighborhood(NODE_FACT, rel.source_fact_id, depth=2)
        nodes = {n["id"]: n for n in payload["nodes"]}
        for edge in payload["edges"]:
            if edge["type"] != g.EDGE_SUPPORTED_BY:
                continue
            fact_node = nodes[edge["source"]]
            ev_node = nodes[edge["target"]]
            owning_fact_id = fact_node["source_id"]
            assert ev_node["metadata"]["fact_id"] == owning_fact_id
            quotes = [e.verbatim_quote for e in real_store.get_evidence(owning_fact_id)]
            assert ev_node["metadata"]["verbatim_quote"] in quotes


def test_every_document_link_matches_the_evidence_doc_id(real_store):
    proj = GraphProjection(real_store)
    payload = proj.neighborhood(NODE_FACT, real_store.relations[0].source_fact_id, depth=2)
    nodes = {n["id"]: n for n in payload["nodes"]}
    for edge in _edges_of(payload, g.EDGE_LOCATED_IN):
        ev_node, doc_node = nodes[edge["source"]], nodes[edge["target"]]
        assert ev_node["metadata"]["doc_id"] == doc_node["source_id"]
        assert doc_node["source_id"] in real_store.ingested_docs


def test_unrelated_facts_get_no_relation_edge(small_store):
    """`c` shares a document with `a` but has no recorded relation. Sharing a
    source is not a relationship."""
    store, a, _b, c = small_store
    payload = build_neighborhood(store, NODE_DOCUMENT, "docA", depth=2)
    rel_edges = [e for e in payload["edges"] if e["type"] not in STRUCTURAL_EDGE_TYPES]
    pairs = {(e["metadata"]["source_fact_id"], e["metadata"]["target_fact_id"])
             for e in rel_edges}
    assert (a.fact_id, c.fact_id) not in pairs
    assert (c.fact_id, a.fact_id) not in pairs


def test_entity_resolution_is_preserved_not_improved(small_store):
    """Two different canonical subjects stay two entity nodes. The graph must
    never merge what the resolver deliberately left apart."""
    store, a, _b, c = small_store
    assert a.subject != c.subject
    payload = build_neighborhood(store, NODE_DOCUMENT, "docA", depth=2)
    entity_ids = {n["source_id"] for n in _by_type(payload, NODE_ENTITY)}
    assert a.subject in entity_ids and c.subject in entity_ids
    assert len(entity_ids) >= 2


def test_projection_never_mutates_the_store(real_store):
    import copy
    before_facts = copy.deepcopy(real_store.facts)
    before_relations = list(real_store.relations)
    build_neighborhood(real_store, NODE_FACT, real_store.relations[0].source_fact_id, depth=3)
    assert real_store.facts == before_facts
    assert real_store.relations == before_relations


# --------------------------------------------------------------------------
# Bounded traversal (spec §16, §38)
# --------------------------------------------------------------------------

def test_depth_zero_returns_only_the_root(real_store):
    root = real_store.relations[0].source_fact_id
    payload = build_neighborhood(real_store, NODE_FACT, root, depth=0)
    assert payload["metadata"]["node_count"] == 1
    assert payload["edges"] == []
    assert payload["root"]["source_id"] == root


def test_depth_is_clamped_to_max_depth(real_store):
    root = real_store.relations[0].source_fact_id
    payload = build_neighborhood(real_store, NODE_FACT, root, depth=99)
    assert payload["metadata"]["depth"] == g.MAX_DEPTH


def test_node_count_grows_monotonically_with_depth(real_store):
    root = real_store.relations[0].source_fact_id
    counts = [build_neighborhood(real_store, NODE_FACT, root, depth=d)["metadata"]["node_count"]
              for d in range(0, 3)]
    assert counts == sorted(counts)
    assert counts[0] == 1


def test_node_cap_is_enforced_and_reported(real_store):
    root = real_store.relations[0].source_fact_id
    payload = build_neighborhood(real_store, NODE_FACT, root, depth=4, max_nodes=12)
    assert payload["metadata"]["node_count"] <= 12
    assert payload["metadata"]["truncated"] is True
    assert payload["metadata"]["truncation_reasons"]


def test_fanout_cap_is_enforced_and_reported(real_store):
    doc_id = next(iter(real_store.ingested_docs))
    payload = build_neighborhood(real_store, NODE_DOCUMENT, doc_id, depth=1, max_fanout=3)
    assert payload["metadata"]["truncated"] is True
    assert any("fan-out" in r for r in payload["metadata"]["truncation_reasons"])


def test_truncation_is_never_silent(real_store):
    root = real_store.relations[0].source_fact_id
    payload = build_neighborhood(real_store, NODE_FACT, root, depth=4, max_nodes=10)
    md = payload["metadata"]
    assert md["truncated"] is True
    assert len(md["truncation_reasons"]) >= 1


def test_metadata_declares_it_is_not_a_source_of_truth(real_store):
    payload = build_neighborhood(real_store, NODE_FACT,
                                 real_store.relations[0].source_fact_id, depth=1)
    assert payload["metadata"]["is_source_of_truth"] is False
    assert "Store" in payload["metadata"]["projection_of"]


# --------------------------------------------------------------------------
# Roots
# --------------------------------------------------------------------------

def test_unknown_ids_return_none(real_store):
    assert build_neighborhood(real_store, NODE_FACT, "nope", depth=1) is None
    assert build_neighborhood(real_store, NODE_ENTITY, "nope", depth=1) is None
    assert build_neighborhood(real_store, NODE_DOCUMENT, "nope", depth=1) is None
    assert build_neighborhood(real_store, NODE_EVIDENCE, "nope#0", depth=1) is None
    assert build_neighborhood(real_store, "bogus_type", "x", depth=1) is None


def test_evidence_root_resolves_and_links_its_document(real_store):
    fact_id = real_store.relations[0].source_fact_id
    payload = build_neighborhood(real_store, NODE_EVIDENCE, f"{fact_id}#0", depth=1)
    assert payload is not None
    assert payload["root"]["type"] == NODE_EVIDENCE
    assert _by_type(payload, NODE_DOCUMENT)


def test_evidence_root_with_out_of_range_index_is_not_found(real_store):
    fact_id = real_store.relations[0].source_fact_id
    assert build_neighborhood(real_store, NODE_EVIDENCE, f"{fact_id}#9999", depth=1) is None


def test_entity_root_lists_its_facts(real_store):
    subject = real_store.facts[real_store.relations[0].source_fact_id].subject
    payload = build_neighborhood(real_store, NODE_ENTITY, subject, depth=1)
    facts = _by_type(payload, NODE_FACT)
    assert facts
    for node in facts:
        assert real_store.facts[node["source_id"]].subject == subject


def test_entity_node_count_is_live_not_stale(real_store):
    subject = real_store.facts[real_store.relations[0].source_fact_id].subject
    expected = sum(1 for f in real_store.facts.values() if f.subject == subject)
    payload = build_neighborhood(real_store, NODE_ENTITY, subject, depth=0)
    assert payload["root"]["metadata"]["fact_count"] == expected


# --------------------------------------------------------------------------
# Incremental ingestion (spec §33, §44)
# --------------------------------------------------------------------------

def test_new_facts_appear_without_any_graph_rebuild(small_store):
    """There is no persisted graph, so a store mutation is visible on the
    next projection with no invalidation step."""
    store, a, _b, _c = small_store
    before = build_neighborhood(store, NODE_ENTITY, a.subject, depth=1)
    before_ids = {n["id"] for n in before["nodes"]}

    fresh = _mkfact(val="999", period="FY2024-25", doc_id="docC", page=3)
    store.facts[fresh.fact_id] = fresh
    store.ingested_docs["docC"] = "new-filing.pdf"
    store.relations.append(
        Relation(a.fact_id, fresh.fact_id, RelationType.SUPERSEDES, 0.9,
                 "temporal_succession", "Later statement updates the earlier one.", {}))

    after = build_neighborhood(store, NODE_ENTITY, a.subject, depth=2)
    after_ids = {n["id"] for n in after["nodes"]}

    assert g.fact_node_id(fresh.fact_id) in after_ids
    assert before_ids <= after_ids, "previously present nodes must survive"
    assert g.document_node_id("docC") in after_ids
    rel_types = {e["type"] for e in after["edges"] if e["type"] not in STRUCTURAL_EDGE_TYPES}
    assert "SUPERSEDES" in rel_types
    ids = [n["id"] for n in after["nodes"]]
    assert len(ids) == len(set(ids)), "re-projection must not duplicate nodes"


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def test_search_finds_entities_and_documents(real_store):
    proj = GraphProjection(real_store)
    subject = real_store.facts[real_store.relations[0].source_fact_id].subject
    results = proj.search(subject[:6], limit=20)
    assert results
    assert all(r["type"] in (NODE_ENTITY, NODE_FACT, NODE_DOCUMENT) for r in results)


def test_search_is_bounded_and_handles_empty_input(real_store):
    proj = GraphProjection(real_store)
    assert proj.search("", limit=10) == []
    assert proj.search("   ", limit=10) == []
    assert len(proj.search("a", limit=5)) <= 5
