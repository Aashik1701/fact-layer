"""
Evidence Lineage — a provenance-chain PROJECTION built ON TOP OF the
existing Knowledge Graph (`fact_layer.graph`), never a second graph engine.

WHAT THIS IS
--------------------------------------------------------------------------
For a stored conclusion — a `Relation`, or a single `Fact` — the focused
chain that answers "why should I trust this": which facts it rests on,
which evidence spans back each fact, and which document/page each span
comes from.

    CONCLUSION (relation, or the fact itself)
        |
     FACT(S)
        |
    EVIDENCE
        |
      PAGE
        |
    DOCUMENT

WHAT THIS IS NOT
--------------------------------------------------------------------------
It is NOT a parallel graph representation. Every node and edge here is
produced by `fact_layer.graph.GraphProjection` — this module calls its
existing, unmodified `neighborhood()` method and unions the results; it
never constructs a `GraphNode`/`GraphEdge` itself. Section 23's rule
("reuse the Knowledge Graph projection... do not create two independent
graph representations") is enforced by import, not by convention.

It does NOT invent a relationship. `relation_lineage()` requires an
already-resolved `Relation` object — looked up by the caller from
`Store.relations` — and never accepts two arbitrary fact ids to synthesize
one from (section 31). A pair of facts with no stored relation between
them is a "comparison blocked" case, which belongs to the Comparability
Investigator (`fact_layer.investigate`), not to this module — see the
module-level note below.

It does NOT invent confidence. The `root_conclusion` block for a relation
carries that relation's own `confidence`/`reason_code`/`explanation`,
copied verbatim — nothing here computes a "lineage confidence" score
(section 30).

WHY "INCOMPARABLE" HAS NO LINEAGE FUNCTION HERE
--------------------------------------------------------------------------
`adjudicate_cluster()`/`_incremental_pairwise_relations()` (fact_layer/
store.py, fact_layer/adjudicate.py) never persist an `UNRELATED` verdict
to `Store.relations` — it is computed and discarded. So there is no
relation row to build lineage from for a genuinely incomparable pair; the
correct answer for that case is the existing Comparability Investigator
(`fact_layer.investigate.investigate(a, b)`), which explains blocking
dimensions without pretending a relationship was found. The frontend
routes to that view directly rather than this module fabricating one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .graph import GraphProjection, NODE_DOCUMENT, NODE_EVIDENCE, NODE_FACT
from .models import Relation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import Store

# Depth 1 from a fact root already yields, per GraphProjection._expand():
# its entity, every evidence span + that span's document, and any directly
# related facts (as bare nodes) — exactly the "fact -> evidence -> page ->
# document" chain this module exists to show, no further hops needed.
_LINEAGE_DEPTH = 1
_LINEAGE_MAX_NODES = 60
_LINEAGE_MAX_FANOUT = 25


@dataclass
class LineageRootConclusion:
    kind: str                          # "fact" | "relation"
    fact_id: Optional[str] = None
    relation_id: Optional[str] = None
    relation: Optional[str] = None
    confidence: Optional[float] = None
    reason_code: Optional[str] = None
    explanation: Optional[str] = None
    decided_by: Optional[str] = None
    source_fact_id: Optional[str] = None
    target_fact_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "fact_id": self.fact_id,
            "relation_id": self.relation_id, "relation": self.relation,
            "confidence": self.confidence, "reason_code": self.reason_code,
            "explanation": self.explanation, "decided_by": self.decided_by,
            "source_fact_id": self.source_fact_id, "target_fact_id": self.target_fact_id,
        }


@dataclass
class LineageResult:
    root_conclusion: LineageRootConclusion
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "root_conclusion": self.root_conclusion.to_dict(),
            "nodes": self.nodes, "edges": self.edges,
            "facts": self.facts, "evidence": self.evidence, "documents": self.documents,
            "metadata": self.metadata,
        }


def _categorize(nodes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    facts = [n for n in nodes if n["type"] == NODE_FACT]
    evidence = [n for n in nodes if n["type"] == NODE_EVIDENCE]
    documents = [n for n in nodes if n["type"] == NODE_DOCUMENT]
    return facts, evidence, documents


def fact_lineage(store: "Store", fact_id: str) -> Optional[LineageResult]:
    """The provenance chain for one fact: its evidence, their documents,
    and any directly related facts. Returns None (-> API 404) for an
    unknown fact_id; never synthesizes."""
    if fact_id not in store.facts:
        return None

    projection = GraphProjection(store)
    neighborhood = projection.neighborhood(
        NODE_FACT, fact_id, depth=_LINEAGE_DEPTH,
        max_nodes=_LINEAGE_MAX_NODES, max_fanout=_LINEAGE_MAX_FANOUT,
    )
    if neighborhood is None:
        return None

    facts, evidence, documents = _categorize(neighborhood["nodes"])
    return LineageResult(
        root_conclusion=LineageRootConclusion(kind="fact", fact_id=fact_id),
        nodes=neighborhood["nodes"], edges=neighborhood["edges"],
        facts=facts, evidence=evidence, documents=documents,
        metadata={**neighborhood["metadata"], "root_kind": "fact"},
    )


def relation_lineage(store: "Store", relation: Relation, relation_id: str) -> LineageResult:
    """The provenance chain for one adjudicated relation: both facts it
    connects, each fact's evidence, and their documents. `relation_id` is
    supplied by the caller (the API layer already computed it to look the
    relation up) rather than recomputed here, so there is exactly one
    place — api.py's `_relation_index()` — that owns relation identity.

    Built from TWO depth-1 fact neighborhoods on one shared
    GraphProjection, unioned by node/edge id. Both fact ids are therefore
    always present in the union, so GraphProjection's own
    `_link_relations_between_present_facts()` step (run inside each
    `neighborhood()` call) already included the connecting relation edge —
    this function only dedupes it, never constructs it.
    """
    projection = GraphProjection(store)
    nodes_by_id: dict[str, dict] = {}
    edges_by_id: dict[str, dict] = {}
    truncated = False
    reasons: set[str] = set()

    for fid in (relation.source_fact_id, relation.target_fact_id):
        sub = projection.neighborhood(
            NODE_FACT, fid, depth=_LINEAGE_DEPTH,
            max_nodes=_LINEAGE_MAX_NODES, max_fanout=_LINEAGE_MAX_FANOUT,
        )
        if sub is None:
            continue   # a relation whose fact no longer resolves; never fabricate a stand-in node
        for n in sub["nodes"]:
            nodes_by_id.setdefault(n["id"], n)
        for e in sub["edges"]:
            edges_by_id.setdefault(e["id"], e)
        truncated = truncated or sub["metadata"]["truncated"]
        reasons.update(sub["metadata"]["truncation_reasons"])

    # Guarantee both fact nodes are present even if one side's own
    # neighborhood call failed to resolve it (should not happen for a
    # relation stored against two real facts, but never silently drop the
    # conclusion's own endpoints).
    facts, evidence, documents = _categorize(list(nodes_by_id.values()))

    root = LineageRootConclusion(
        kind="relation", relation_id=relation_id, relation=relation.relation.value,
        confidence=relation.confidence, reason_code=relation.reason_code,
        explanation=relation.explanation, decided_by=relation.decided_by,
        source_fact_id=relation.source_fact_id, target_fact_id=relation.target_fact_id,
    )
    return LineageResult(
        root_conclusion=root,
        nodes=sorted(nodes_by_id.values(), key=lambda n: (n["depth"], n["id"])),
        edges=sorted(edges_by_id.values(), key=lambda e: e["id"]),
        facts=facts, evidence=evidence, documents=documents,
        metadata={
            "node_count": len(nodes_by_id), "edge_count": len(edges_by_id),
            "truncated": truncated, "truncation_reasons": sorted(reasons),
            "projection_of": "fact_layer.graph.GraphProjection (unioned over both facts)",
            "is_source_of_truth": False, "root_kind": "relation",
        },
    )


__all__ = ["LineageRootConclusion", "LineageResult", "fact_lineage", "relation_lineage"]
