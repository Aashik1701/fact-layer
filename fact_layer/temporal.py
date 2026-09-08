"""
Temporal Knowledge — a fact-history PROJECTION over the existing fact layer.

WHAT THIS IS
--------------------------------------------------------------------------
For one canonical (subject, measure) — exactly `Fact.cluster_key()`, so
entity/measure resolution is reused verbatim, never re-derived — a
deterministic, chronologically-ordered view of every verified fact the
knowledge layer holds for it, grouped by the qualifiers that make two
figures actually comparable (scope, modality), with any EXISTING relation
between two points in the same series surfaced alongside them.

WHAT THIS IS NOT
--------------------------------------------------------------------------
It is NOT a second store and NOT a time-series database. Every point is a
live `Fact` from `Store.facts`; there is nothing to keep in sync, so an
incremental ingest is reflected on the next call with no rebuild step
(same guarantee `fact_layer.graph` makes, same reason).

It does NOT invent temporal relationships. A relation attached to two
points in a series is copied verbatim from `Store.relations` — if
`adjudicate()` never recorded a relation between two facts, the projection
reports "no established relationship" between them, never a guess. Only
`Store.relations`'s existing `SUPERSEDES` edges are ever labeled
supersession; a fact being merely *newer* is not supersession.

It does NOT interpolate. A period with no fact is simply absent from the
series — there is no synthetic point, no estimated value, no smoothing.

PERIOD ORDERING
--------------------------------------------------------------------------
The sort key is `(period.start, period.end)`, both already computed by
`fact_layer.normalize.parse_period()` — this module adds no date parsing
of its own. A fact whose period has no parsed `start` (unstated, or a
phrase `parse_period()` could not resolve) is never guessed into a
position on the axis; it is reported separately as `ambiguous_periods`.
`period_kind_display` (fiscal year / quarter / half-year / as-of /
unknown) is a presentation label derived from `PeriodKind` + the period's
own verbatim `label` — it never feeds the sort key, so it cannot
reclassify what `normalize.py` already determined.

GROUPING
--------------------------------------------------------------------------
Points are grouped into series by `(scope, modality)` — standalone vs.
consolidated, and reported vs. estimated/projected/guidance, are never
merged into one series (sections 7, 37, 38). Within a series, points are
listed chronologically; nothing is picked or discarded when a
scope+modality+period collision occurs (multiple documents reporting the
same figure) — both points are kept, and any relation between them (from
`Store.relations`) is surfaced on both.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .models import Fact, PeriodKind, Quantity, Relation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .store import Store


# --------------------------------------------------------------------------
# Presentation helpers — never used to decide chronological order
# --------------------------------------------------------------------------

def _period_kind_display(fact: Fact) -> str:
    period = fact.qualifiers.period
    if period is None:
        return "unstated"
    if period.start is None:
        return "unknown"
    if period.kind == PeriodKind.INSTANT:
        return "as_of"
    label = (period.label or "").strip().lower()
    if label.startswith("q") and len(label) > 1 and label[1].isdigit():
        return "quarter"
    if "half" in label or label.startswith("h1") or label.startswith("h2"):
        return "half_year"
    return "fiscal_year"


def _value_display(fact: Fact) -> dict:
    v = fact.value
    if isinstance(v, Quantity):
        return {
            "raw": v.raw or str(v.value), "normalized": str(v.value),
            "unit": v.unit, "currency": v.currency,
        }
    return {"raw": str(v), "normalized": str(v), "unit": None, "currency": None}


def _relation_id(rel: Relation) -> str:
    """Byte-identical to graph.py's `_relation_edge_id()` (same seed, same
    truncation) so a relation_id surfaced here always matches the one
    `GET /relations/{relation_id}` and the graph/lineage projections use —
    this module does not own relation identity, it only needs to name one
    consistently."""
    seed = f"{rel.source_fact_id}|{rel.target_fact_id}|{rel.relation.value}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------

@dataclass
class RelatedPoint:
    fact_id: str
    relation_id: str
    relation: str
    confidence: float
    reason_code: str
    explanation: str

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id, "relation_id": self.relation_id,
            "relation": self.relation, "confidence": self.confidence,
            "reason_code": self.reason_code, "explanation": self.explanation,
        }


@dataclass
class HistoryPoint:
    fact_id: str
    period_label: str
    period_kind: str
    period_start: Optional[str]
    period_end: Optional[str]
    value: dict
    value_kind: str
    scope: str
    modality: str
    issuer: Optional[str]
    segment: Optional[str]
    geography: Optional[str]
    basis: Optional[str]
    confidence: float
    evidence_verified: bool
    value_verification: Optional[str]
    doc_id: Optional[str]
    doc_filename: Optional[str]
    page: Optional[int]
    related_points: list[RelatedPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "period": {
                "label": self.period_label, "kind": self.period_kind,
                "start": self.period_start, "end": self.period_end,
            },
            "value": self.value, "value_kind": self.value_kind,
            "scope": self.scope, "modality": self.modality,
            "issuer": self.issuer, "segment": self.segment,
            "geography": self.geography, "basis": self.basis,
            "confidence": self.confidence,
            "verification": {
                "evidence_verified": self.evidence_verified,
                "value_verification": self.value_verification,
            },
            "source": {
                "doc_id": self.doc_id, "doc_filename": self.doc_filename, "page": self.page,
            },
            "related_points": [r.to_dict() for r in self.related_points],
        }


@dataclass
class HistorySeries:
    scope: str
    modality: str
    points: list[HistoryPoint]

    def to_dict(self) -> dict:
        return {
            "scope": self.scope, "modality": self.modality,
            "points": [p.to_dict() for p in self.points],
        }


@dataclass
class EntityHistory:
    subject: str
    measure: str
    total_facts: int
    series: list[HistorySeries] = field(default_factory=list)
    ambiguous_period_points: list[HistoryPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "subject": self.subject, "measure": self.measure,
            "total_facts": self.total_facts,
            "series": [s.to_dict() for s in self.series],
            "ambiguous_period_points": [p.to_dict() for p in self.ambiguous_period_points],
            "metadata": {
                "projection_of": "Store.facts / Store.relations",
                "is_source_of_truth": False,
                "interpolated": False,
            },
        }


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

def entity_measures(store: "Store", subject: str) -> list[str]:
    """Canonical measures with at least one fact for `subject` — the
    "pick a measure" discovery step, so the caller never has to guess a
    measure id."""
    return sorted({f.measure for f in store.facts.values() if f.subject == subject})


def _sort_key(fact: Fact):
    p = fact.qualifiers.period
    if p is None or p.start is None:
        return None
    return (p.start, p.end or p.start)


def _filename(store: "Store", doc_id: Optional[str]) -> Optional[str]:
    if doc_id is None:
        return None
    return store.ingested_docs.get(doc_id, doc_id)


def _point(store: "Store", fact: Fact) -> HistoryPoint:
    period = fact.qualifiers.period
    ev = fact.evidence
    return HistoryPoint(
        fact_id=fact.fact_id,
        period_label=period.label if period else "",
        period_kind=_period_kind_display(fact),
        period_start=period.start.isoformat() if period and period.start else None,
        period_end=period.end.isoformat() if period and period.end else None,
        value=_value_display(fact), value_kind=fact.value_kind.value,
        scope=fact.qualifiers.scope.value, modality=fact.modality.value,
        issuer=fact.qualifiers.issuer, segment=fact.qualifiers.segment,
        geography=fact.qualifiers.geography, basis=fact.qualifiers.basis,
        confidence=fact.confidence,
        evidence_verified=bool(ev and ev.verified),
        value_verification=fact.value_verification or None,
        doc_id=ev.doc_id if ev else None,
        doc_filename=_filename(store, ev.doc_id if ev else None),
        page=ev.page if ev else None,
    )


def entity_history(store: "Store", subject: str, measure: str) -> EntityHistory:
    """The full timeline for one canonical (subject, measure). Never
    raises for an unknown subject/measure — returns an EntityHistory with
    zero facts; the API layer decides whether that is a 404 (unknown
    subject entirely) or a legitimately empty series (known subject, this
    measure has no facts)."""
    facts = [f for f in store.facts.values() if f.subject == subject and f.measure == measure]
    fact_ids = {f.fact_id for f in facts}

    # Relations strictly between two facts already in THIS history set —
    # never pulled from outside it, and never inferred: copied verbatim
    # from Store.relations, matching graph.py's own "relation edges come
    # only from Store.relations" rule.
    related_by_fact: dict[str, list[RelatedPoint]] = {}
    for rel in store.relations:
        if rel.source_fact_id in fact_ids and rel.target_fact_id in fact_ids:
            rid = _relation_id(rel)
            for owner, other in ((rel.source_fact_id, rel.target_fact_id),
                                 (rel.target_fact_id, rel.source_fact_id)):
                related_by_fact.setdefault(owner, []).append(RelatedPoint(
                    fact_id=other, relation_id=rid, relation=rel.relation.value,
                    confidence=rel.confidence, reason_code=rel.reason_code,
                    explanation=rel.explanation,
                ))

    points_by_series: dict[tuple[str, str], list[tuple[tuple, HistoryPoint]]] = {}
    ambiguous: list[HistoryPoint] = []

    for fact in facts:
        point = _point(store, fact)
        point.related_points = sorted(
            related_by_fact.get(fact.fact_id, []), key=lambda r: r.fact_id)
        key = _sort_key(fact)
        if key is None:
            ambiguous.append(point)
            continue
        series_key = (fact.qualifiers.scope.value, fact.modality.value)
        points_by_series.setdefault(series_key, []).append((key, point))

    series: list[HistorySeries] = []
    for (scope, modality), entries in sorted(points_by_series.items()):
        entries.sort(key=lambda e: (e[0], e[1].fact_id))
        series.append(HistorySeries(scope=scope, modality=modality,
                                    points=[p for _k, p in entries]))
    ambiguous.sort(key=lambda p: p.fact_id)

    return EntityHistory(
        subject=subject, measure=measure, total_facts=len(facts),
        series=series, ambiguous_period_points=ambiguous,
    )


__all__ = [
    "HistoryPoint", "HistorySeries", "EntityHistory", "RelatedPoint",
    "entity_measures", "entity_history",
]
