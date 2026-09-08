"""
Knowledge-graph PROJECTION over the existing fact layer.

WHAT THIS IS
--------------------------------------------------------------------------
A read-only, bounded view that connects things the knowledge layer already
knows about:

    ENTITY --HAS_FACT--> FACT --SUPPORTED_BY--> EVIDENCE --LOCATED_IN--> DOCUMENT
                          |
                          +--CORROBORATES / CONTRADICTS / APPARENT_CONFLICT /
                             SUPERSEDES / AGGREGATES_INTO--> FACT

WHAT THIS IS NOT
--------------------------------------------------------------------------
It is NOT a second source of truth and NOT a reasoning engine. There is no
graph database, no `graph.json`, and nothing to keep in sync: every node and
edge is derived on demand from `Store.facts`, `Store.relations`,
`Store.get_evidence()` and `Store.ingested_docs`. Ingest another document
and the next projection contains it, because there is no separate dataset
that could go stale.

Three rules the projection must never break:

  1. **Relation edges come only from `Store.relations`.** The graph never
     decides that two facts corroborate, contradict or supersede — it draws
     what `adjudicate()` already recorded. If no relation exists, no edge is
     drawn, no matter how similar two facts look.
  2. **A retrieval candidate is not a relationship.** Candidates are not
     rendered here at all; the retrieval panel and the Comparability
     Investigator own that story.
  3. **Entity resolution is preserved, never improved.** Two facts share an
     ENTITY node exactly when `fact.subject` is already the same canonical
     string. The graph never merges what the resolver left apart.

BOUNDEDNESS
--------------------------------------------------------------------------
Traversal is a breadth-first walk from one root node with a hard depth cap,
a per-node fan-out cap and a total node cap. Hitting any of them sets
`truncated` and records why — the graph says "showing a 2-hop neighbourhood,
expand from a node to continue", never silently drops half the picture. A
552-fact corpus would be an unreadable spider web rendered whole, and a
larger one would be unrenderable; bounded neighbourhoods are the design, not
a limitation to apologise for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from .models import Evidence, Fact, Quantity, Relation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import Store


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

NODE_ENTITY = "entity"
NODE_FACT = "fact"
NODE_EVIDENCE = "evidence"
NODE_DOCUMENT = "document"

NODE_TYPES = (NODE_ENTITY, NODE_FACT, NODE_EVIDENCE, NODE_DOCUMENT)

EDGE_HAS_FACT = "HAS_FACT"
EDGE_SUPPORTED_BY = "SUPPORTED_BY"
EDGE_LOCATED_IN = "LOCATED_IN"

# Structural edges the projection itself creates (provenance wiring).
# Relation edge types are NOT listed here: they are whatever
# `RelationType` values the adjudicator actually recorded, uppercased, so
# the graph can never rename or invent relation semantics.
STRUCTURAL_EDGE_TYPES = (EDGE_HAS_FACT, EDGE_SUPPORTED_BY, EDGE_LOCATED_IN)

DEFAULT_DEPTH = 2
MAX_DEPTH = 4
DEFAULT_MAX_NODES = 150
DEFAULT_MAX_FANOUT = 25


# --------------------------------------------------------------------------
# Node ids
#
# Type-prefixed so a fact and a document can never collide, and so the
# frontend can route a click without a lookup table. Evidence has no id of
# its own in the domain model, so it is addressed by its owning fact plus
# its index in `Store.get_evidence()`, whose order that method documents as
# stable.
# --------------------------------------------------------------------------

def entity_node_id(subject: str) -> str:
    return f"entity:{subject}"


def fact_node_id(fact_id: str) -> str:
    return f"fact:{fact_id}"


def document_node_id(doc_id: str) -> str:
    return f"document:{doc_id}"


def evidence_node_id(fact_id: str, index: int) -> str:
    return f"evidence:{fact_id}#{index}"


@dataclass
class GraphNode:
    id: str
    type: str
    label: str
    subtitle: str = ""
    source_id: str = ""          # the authoritative id this projects from
    depth: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "label": self.label,
            "subtitle": self.subtitle, "source_id": self.source_id,
            "depth": self.depth, "metadata": dict(self.metadata),
        }


@dataclass
class GraphEdge:
    id: str
    source: str
    target: str
    type: str
    label: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "source": self.source, "target": self.target,
            "type": self.type, "label": self.label, "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------
# Display helpers — presentation only, never used to make a decision
# --------------------------------------------------------------------------

def _value_display(fact: Fact) -> str:
    v = fact.value
    if isinstance(v, Quantity):
        raw = v.raw or str(v.value)
        unit = v.currency or v.unit or ""
        return f"{raw} {unit}".strip()
    return str(v)


def _fact_subtitle(fact: Fact) -> str:
    parts = [_value_display(fact)]
    if fact.qualifiers.period and fact.qualifiers.period.label:
        parts.append(fact.qualifiers.period.label)
    scope = fact.qualifiers.scope.value if fact.qualifiers.scope else None
    if scope and scope != "unknown":
        parts.append(scope)
    return " · ".join(p for p in parts if p)


def _short_quote(quote: str, limit: int = 120) -> str:
    quote = (quote or "").strip()
    return quote if len(quote) <= limit else quote[: limit - 1] + "…"


def _fact_node(fact: Fact, depth: int) -> GraphNode:
    return GraphNode(
        id=fact_node_id(fact.fact_id), type=NODE_FACT,
        label=fact.measure, subtitle=_fact_subtitle(fact),
        source_id=fact.fact_id, depth=depth,
        metadata={
            "subject": fact.subject,
            "measure": fact.measure,
            "value": _value_display(fact),
            "value_kind": fact.value_kind.value,
            "period": fact.qualifiers.period.label if fact.qualifiers.period else None,
            "scope": fact.qualifiers.scope.value if fact.qualifiers.scope else None,
            "issuer": fact.qualifiers.issuer,
            "segment": fact.qualifiers.segment,
            "modality": fact.modality.value,
            "confidence": fact.confidence,
            "value_verification": fact.value_verification or None,
            "doc_id": fact.evidence.doc_id if fact.evidence else None,
            "page": fact.evidence.page if fact.evidence else None,
        },
    )


def _entity_node(subject: str, fact_count: int, doc_count: int, depth: int) -> GraphNode:
    return GraphNode(
        id=entity_node_id(subject), type=NODE_ENTITY,
        label=subject,
        subtitle=f"{fact_count} fact{'s' if fact_count != 1 else ''}",
        source_id=subject, depth=depth,
        metadata={"fact_count": fact_count, "document_count": doc_count},
    )


def _document_node(doc_id: str, filename: str, fact_count: int, depth: int) -> GraphNode:
    return GraphNode(
        id=document_node_id(doc_id), type=NODE_DOCUMENT,
        label=filename or doc_id,
        subtitle=f"{fact_count} fact{'s' if fact_count != 1 else ''}",
        source_id=doc_id, depth=depth,
        metadata={"doc_id": doc_id, "filename": filename, "fact_count": fact_count},
    )


def _evidence_node(fact_id: str, index: int, ev: Evidence, filename: str, depth: int) -> GraphNode:
    return GraphNode(
        id=evidence_node_id(fact_id, index), type=NODE_EVIDENCE,
        label=f"Page {ev.page}",
        subtitle=_short_quote(ev.verbatim_quote, 60),
        source_id=fact_id, depth=depth,
        metadata={
            "fact_id": fact_id,
            "doc_id": ev.doc_id,
            "filename": filename,
            "page": ev.page,
            "verbatim_quote": ev.verbatim_quote,
            "char_start": ev.char_start,
            "char_end": ev.char_end,
            "bbox": list(ev.bbox) if ev.bbox else None,
            "verified": ev.verified,
            "table_id": ev.table_id,
            "row_label": getattr(ev, "row_label", None),
            "column_header": getattr(ev, "column_header", None),
        },
    )


def _relation_edge_id(rel: Relation) -> str:
    import hashlib
    seed = f"{rel.source_fact_id}|{rel.target_fact_id}|{rel.relation.value}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


def _relation_edge(rel: Relation) -> GraphEdge:
    """A relation edge carries the adjudicator's OWN confidence, reason code
    and explanation. The graph computes none of these; a value here that the
    adjudicator did not produce would be a fabricated finding."""
    rtype = rel.relation.value.upper()
    return GraphEdge(
        id=f"rel:{_relation_edge_id(rel)}",
        source=fact_node_id(rel.source_fact_id),
        target=fact_node_id(rel.target_fact_id),
        type=rtype, label=rtype.replace("_", " "),
        metadata={
            "relation_id": _relation_edge_id(rel),
            "relation": rel.relation.value,
            "confidence": rel.confidence,
            "reason_code": rel.reason_code,
            "explanation": rel.explanation,
            "decided_by": rel.decided_by,
            "qualifier_diff": {k: list(v) for k, v in (rel.qualifier_diff or {}).items()},
            "source_fact_id": rel.source_fact_id,
            "target_fact_id": rel.target_fact_id,
        },
    )


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

class GraphProjection:
    """Derived indices over one `Store`. Built per request and thrown away —
    there is deliberately nothing persistent to invalidate, which is what
    makes the projection correct after an incremental ingest without any
    rebuild step."""

    def __init__(self, store: "Store") -> None:
        self.store = store
        self.facts_by_subject: dict[str, list[Fact]] = {}
        self.facts_by_doc: dict[str, list[Fact]] = {}
        self.relations_by_fact: dict[str, list[Relation]] = {}

        for fact in store.facts.values():
            self.facts_by_subject.setdefault(fact.subject, []).append(fact)
            if fact.evidence is not None:
                self.facts_by_doc.setdefault(fact.evidence.doc_id, []).append(fact)
        for rel in store.relations:
            self.relations_by_fact.setdefault(rel.source_fact_id, []).append(rel)
            self.relations_by_fact.setdefault(rel.target_fact_id, []).append(rel)

        # Stable ordering everywhere so the same store always projects the
        # same graph — required for the determinism tests and for a UI that
        # should not reshuffle between two identical requests.
        for bucket in self.facts_by_subject.values():
            bucket.sort(key=lambda f: f.fact_id)
        for bucket in self.facts_by_doc.values():
            bucket.sort(key=lambda f: f.fact_id)
        for bucket in self.relations_by_fact.values():
            bucket.sort(key=lambda r: (r.relation.value, r.source_fact_id, r.target_fact_id))

    # ---- node construction ------------------------------------------------

    def _filename(self, doc_id: str) -> str:
        return self.store.ingested_docs.get(doc_id, doc_id)

    def _entity_doc_count(self, subject: str) -> int:
        return len({f.evidence.doc_id for f in self.facts_by_subject.get(subject, [])
                    if f.evidence is not None})

    def make_entity_node(self, subject: str, depth: int) -> GraphNode:
        return _entity_node(subject, len(self.facts_by_subject.get(subject, [])),
                            self._entity_doc_count(subject), depth)

    def make_document_node(self, doc_id: str, depth: int) -> GraphNode:
        return _document_node(doc_id, self._filename(doc_id),
                              len(self.facts_by_doc.get(doc_id, [])), depth)

    def evidence_for(self, fact_id: str) -> list[tuple[int, Evidence]]:
        """Every span backing a fact, via the sanctioned read path — a fact
        merged from several spans carries only one on the object itself, so
        reading `fact.evidence` here would silently under-report provenance,
        which is the one thing this graph exists to get right."""
        return list(enumerate(self.store.get_evidence(fact_id)))

    # ---- traversal --------------------------------------------------------

    def neighborhood(
        self, root_type: str, root_id: str, depth: int = DEFAULT_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES, max_fanout: int = DEFAULT_MAX_FANOUT,
    ) -> dict:
        """Bounded BFS from one root. Returns the API payload."""
        depth = max(0, min(int(depth), MAX_DEPTH))
        nodes: dict[str, GraphNode] = {}
        edges: dict[str, GraphEdge] = {}
        truncation: list[str] = []

        root = self._root_node(root_type, root_id)
        if root is None:
            return None
        nodes[root.id] = root

        frontier = [(root, 0)]
        while frontier:
            current, d = frontier.pop(0)
            if d >= depth:
                continue
            expanded, hit_fanout, hit_cap = self._expand(
                current, d + 1, nodes, edges, max_fanout, max_nodes)
            if hit_fanout:
                truncation.append(f"fan-out limit ({max_fanout}) reached at {current.id}")
            if hit_cap:
                truncation.append(f"node limit ({max_nodes}) reached")
            for node in expanded:
                frontier.append((node, d + 1))
            if hit_cap:
                break

        # A relation edge is drawn only when BOTH of its facts are already in
        # the neighbourhood. Pulling in the far side would silently widen the
        # graph past the depth the caller asked for.
        self._link_relations_between_present_facts(nodes, edges)
        # A cap hit can leave an edge pointing at a node that never made it
        # in. Drop those rather than emit a dangling reference the renderer
        # would have to guess about.
        edges = {eid: e for eid, e in edges.items()
                 if e.source in nodes and e.target in nodes}

        return {
            "root": root.to_dict(),
            "nodes": [n.to_dict() for n in sorted(nodes.values(), key=lambda n: (n.depth, n.id))],
            "edges": [e.to_dict() for e in sorted(edges.values(), key=lambda e: e.id)],
            "metadata": {
                "depth": depth,
                "max_depth": MAX_DEPTH,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "truncated": bool(truncation),
                "truncation_reasons": sorted(set(truncation)),
                "max_nodes": max_nodes,
                "max_fanout": max_fanout,
                "projection_of": "Store.facts / Store.relations / Store.get_evidence",
                "is_source_of_truth": False,
            },
        }

    def _root_node(self, root_type: str, root_id: str) -> Optional[GraphNode]:
        if root_type == NODE_FACT:
            fact = self.store.facts.get(root_id)
            return _fact_node(fact, 0) if fact else None
        if root_type == NODE_ENTITY:
            if root_id not in self.facts_by_subject:
                return None
            return self.make_entity_node(root_id, 0)
        if root_type == NODE_DOCUMENT:
            if root_id not in self.store.ingested_docs:
                return None
            return self.make_document_node(root_id, 0)
        if root_type == NODE_EVIDENCE:
            # "fact_id#index"
            fact_id, _, raw_index = root_id.partition("#")
            if fact_id not in self.store.facts:
                return None
            spans = self.evidence_for(fact_id)
            try:
                index = int(raw_index or 0)
            except ValueError:
                return None
            match = next((ev for i, ev in spans if i == index), None)
            if match is None:
                return None
            return _evidence_node(fact_id, index, match, self._filename(match.doc_id), 0)
        return None

    def _expand(
        self, node: GraphNode, depth: int, nodes: dict[str, GraphNode],
        edges: dict[str, GraphEdge], max_fanout: int, max_nodes: int,
    ) -> tuple[list[GraphNode], bool, bool]:
        """One hop out of `node`. Returns (newly added, hit_fanout, hit_cap).

        The node cap is enforced HERE, at insertion, not by the caller after
        the fact: a single expansion can produce dozens of nodes, so checking
        only once the expansion has returned lets the graph overshoot the
        limit it promised — which is exactly the unbounded render that the
        bounded-neighbourhood design exists to prevent.
        """
        added: list[GraphNode] = []
        hit_fanout = False
        state = {"hit_cap": False}

        def put(new_node: GraphNode) -> Optional[GraphNode]:
            existing = nodes.get(new_node.id)
            if existing is not None:
                return existing
            if len(nodes) >= max_nodes:
                state["hit_cap"] = True
                return None
            nodes[new_node.id] = new_node
            added.append(new_node)
            return new_node

        def link(edge: GraphEdge) -> None:
            edges.setdefault(edge.id, edge)

        def put_evidence(fact_id: str, index: int, ev: Evidence, owner_id: str) -> None:
            """Add an evidence node AND its document in one step.

            A document is the terminus of a provenance chain, not a further
            hop of discovery: an evidence node whose document is one hop out
            of range renders as a dangling leaf that cannot answer "which
            document did this come from?", which is the single question the
            graph exists to answer. Documents are a small closed set
            (`Store.ingested_docs`), so completing every chain costs a
            handful of nodes rather than an unbounded expansion."""
            if put(_evidence_node(fact_id, index, ev, self._filename(ev.doc_id), depth)) is None:
                return
            link(GraphEdge(
                id=f"supported_by:{fact_id}#{index}",
                source=owner_id, target=evidence_node_id(fact_id, index),
                type=EDGE_SUPPORTED_BY, label="supported by",
            ))
            if ev.doc_id and put(self.make_document_node(ev.doc_id, depth)) is not None:
                link(GraphEdge(
                    id=f"located_in:evidence:{fact_id}#{index}->{ev.doc_id}",
                    source=evidence_node_id(fact_id, index),
                    target=document_node_id(ev.doc_id),
                    type=EDGE_LOCATED_IN, label="located in",
                ))

        if node.type == NODE_ENTITY:
            bucket = self.facts_by_subject.get(node.source_id, [])
            if len(bucket) > max_fanout:
                hit_fanout = True
            for fact in bucket[:max_fanout]:
                if put(_fact_node(fact, depth)) is None:
                    break
                link(GraphEdge(
                    id=f"has_fact:{node.source_id}->{fact.fact_id}",
                    source=node.id, target=fact_node_id(fact.fact_id),
                    type=EDGE_HAS_FACT, label="has fact",
                ))

        elif node.type == NODE_FACT:
            fact = self.store.facts.get(node.source_id)
            if fact is None:
                return added, hit_fanout
            if put(self.make_entity_node(fact.subject, depth)) is not None:
                link(GraphEdge(
                    id=f"has_fact:{fact.subject}->{fact.fact_id}",
                    source=entity_node_id(fact.subject), target=node.id,
                    type=EDGE_HAS_FACT, label="has fact",
                ))
            spans = self.evidence_for(fact.fact_id)
            if len(spans) > max_fanout:
                hit_fanout = True
            for index, ev in spans[:max_fanout]:
                put_evidence(fact.fact_id, index, ev, node.id)
            # Related facts — authoritative relations only.
            related = self.relations_by_fact.get(fact.fact_id, [])
            if len(related) > max_fanout:
                hit_fanout = True
            for rel in related[:max_fanout]:
                other_id = (rel.target_fact_id if rel.source_fact_id == fact.fact_id
                            else rel.source_fact_id)
                other = self.store.facts.get(other_id)
                if other is None:
                    continue
                if put(_fact_node(other, depth)) is None:
                    break

        elif node.type == NODE_EVIDENCE:
            doc_id = node.metadata.get("doc_id")
            if doc_id and put(self.make_document_node(doc_id, depth)) is not None:
                link(GraphEdge(
                    id=f"located_in:{node.id}->{doc_id}",
                    source=node.id, target=document_node_id(doc_id),
                    type=EDGE_LOCATED_IN, label="located in",
                ))

        elif node.type == NODE_DOCUMENT:
            bucket = self.facts_by_doc.get(node.source_id, [])
            if len(bucket) > max_fanout:
                hit_fanout = True
            for fact in bucket[:max_fanout]:
                fnode = put(_fact_node(fact, depth))
                if fnode is None:
                    break
                for index, ev in self.evidence_for(fact.fact_id):
                    if ev.doc_id != node.source_id:
                        continue
                    put_evidence(fact.fact_id, index, ev, fnode.id)
                    break

        return added, hit_fanout, state["hit_cap"]

    def _link_relations_between_present_facts(
        self, nodes: dict[str, GraphNode], edges: dict[str, GraphEdge],
    ) -> None:
        present = {n.source_id for n in nodes.values() if n.type == NODE_FACT}
        for rel in self.store.relations:
            if rel.source_fact_id in present and rel.target_fact_id in present:
                edge = _relation_edge(rel)
                edges.setdefault(edge.id, edge)

    # ---- search -----------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Case-insensitive substring match over entities, facts and
        documents. Reuses nothing clever on purpose — this is a jump-to
        affordance for a bounded corpus, not a search engine."""
        q = (query or "").strip().lower()
        if not q:
            return []
        out: list[dict] = []
        for subject in sorted(self.facts_by_subject):
            if q in subject.lower():
                n = len(self.facts_by_subject[subject])
                out.append({"type": NODE_ENTITY, "id": subject, "label": subject,
                            "subtitle": f"{n} fact{'s' if n != 1 else ''}"})
        for doc_id in sorted(self.store.ingested_docs):
            filename = self.store.ingested_docs[doc_id]
            if q in filename.lower() or q in doc_id.lower():
                n = len(self.facts_by_doc.get(doc_id, []))
                out.append({"type": NODE_DOCUMENT, "id": doc_id, "label": filename,
                            "subtitle": f"{n} fact{'s' if n != 1 else ''}"})
        for fact in sorted(self.store.facts.values(), key=lambda f: f.fact_id):
            if q in fact.measure.lower() or q in fact.subject.lower():
                out.append({"type": NODE_FACT, "id": fact.fact_id,
                            "label": f"{fact.subject} · {fact.measure}",
                            "subtitle": _fact_subtitle(fact)})
            if len(out) >= limit * 3:
                break
        return out[:limit]


def build_neighborhood(
    store: "Store", root_type: str, root_id: str, depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES, max_fanout: int = DEFAULT_MAX_FANOUT,
) -> Optional[dict]:
    return GraphProjection(store).neighborhood(
        root_type, root_id, depth=depth, max_nodes=max_nodes, max_fanout=max_fanout)


__all__ = [
    "GraphProjection", "GraphNode", "GraphEdge", "build_neighborhood",
    "NODE_TYPES", "NODE_ENTITY", "NODE_FACT", "NODE_EVIDENCE", "NODE_DOCUMENT",
    "EDGE_HAS_FACT", "EDGE_SUPPORTED_BY", "EDGE_LOCATED_IN",
    "STRUCTURAL_EDGE_TYPES", "MAX_DEPTH", "DEFAULT_DEPTH",
    "entity_node_id", "fact_node_id", "evidence_node_id", "document_node_id",
]
