"""
The comparability gate.

This is the intellectual core of the submission. Before any two facts are
compared by value, they must pass through here. The gate answers a single
question: *are these two claims even talking about the same thing?*

Almost every failure mode in naive fact-checking is a comparability failure
dressed up as a contradiction:

    Rs 120.4 Cr (FY24)          vs Rs 32.1 Cr (Q1 FY25)   -> disjoint periods
    Rs 120.4 Cr (standalone)    vs Rs 145.9 Cr (consolidated) -> different scope
    Rs 120.4 Cr                 vs USD 14.5 mn            -> different currency
    Director active (31-3-2024) vs resigned (12-3-2025)   -> temporal succession

None of those are contradictions. A system that reports them as contradictions
is worse than useless, because it buries the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import Fact, Modality, Period, PeriodKind, Quantity, Scope, ValueKind


class PeriodRelation(str, Enum):
    EQUAL = "equal"
    SUBSUMES = "subsumes"           # A contains B
    SUBSUMED_BY = "subsumed_by"     # A inside B
    OVERLAPS = "overlaps"
    DISJOINT = "disjoint"
    SUCCEEDS = "succeeds"           # A is a later instant than B
    PRECEDES = "precedes"
    UNKNOWN = "unknown"


def compare_periods(a: Optional[Period], b: Optional[Period]) -> PeriodRelation:
    if a is None or b is None or a.start is None or b.start is None:
        return PeriodRelation.UNKNOWN
    if a.kind == PeriodKind.INSTANT and b.kind == PeriodKind.INSTANT:
        if a.end == b.end:
            return PeriodRelation.EQUAL
        return PeriodRelation.SUCCEEDS if a.end > b.end else PeriodRelation.PRECEDES
    if a.start == b.start and a.end == b.end:
        return PeriodRelation.EQUAL
    if a.start <= b.start and a.end >= b.end:
        return PeriodRelation.SUBSUMES
    if b.start <= a.start and b.end >= a.end:
        return PeriodRelation.SUBSUMED_BY
    if a.end < b.start or b.end < a.start:
        return PeriodRelation.DISJOINT
    return PeriodRelation.OVERLAPS


class Verdict(str, Enum):
    COMPARABLE = "comparable"
    AGGREGATION_CANDIDATE = "aggregation_candidate"   # Q1 inside FY -> sum check
    TEMPORAL_SUCCESSION = "temporal_succession"       # point-in-time, later wins
    INCOMPARABLE_PERIOD = "incomparable_period"
    INCOMPARABLE_SCOPE = "incomparable_scope"
    INCOMPARABLE_UNIT = "incomparable_unit"
    INCOMPARABLE_SEGMENT = "incomparable_segment"
    INCOMPARABLE_BASIS = "incomparable_basis"
    INCOMPARABLE_KIND = "incomparable_kind"
    INCOMPARABLE_ISSUER = "incomparable_issuer"   # differing issuers, at least one a projection


@dataclass
class GateResult:
    verdict: Verdict
    reason_code: str
    explanation: str
    period_relation: PeriodRelation = PeriodRelation.UNKNOWN
    qualifier_diff: dict = None
    cross_issuer: bool = False   # both facts have differing issuers, even when not short-circuited here

    @property
    def comparable(self) -> bool:
        return self.verdict == Verdict.COMPARABLE


# Currencies are only comparable when identical. We deliberately do NOT apply an
# FX rate: the correct rate depends on the reporting date and the rate source,
# neither of which the document states. Silently converting would manufacture
# false contradictions. We surface it as incomparable and explain why.
def _unit_compatible(a: Quantity, b: Quantity) -> tuple[bool, str]:
    if a.unit != b.unit:
        return False, f"units differ ({a.unit} vs {b.unit})"
    if a.currency != b.currency:
        return False, f"currencies differ ({a.currency} vs {b.currency}); no FX rate is stated in the source"
    return True, ""


def gate(a: Fact, b: Fact) -> GateResult:
    """Decide whether facts `a` and `b` may be compared by value."""
    qdiff = a.qualifiers.diff(b.qualifiers)

    if a.value_kind != b.value_kind:
        return GateResult(
            Verdict.INCOMPARABLE_KIND, "value_kind_mismatch",
            f"One fact is a {a.value_kind.value} and the other a {b.value_kind.value}.",
            qualifier_diff=qdiff,
        )

    # --- scope: standalone vs consolidated are both true simultaneously ------
    sa, sb = a.qualifiers.scope, b.qualifiers.scope
    if sa != sb and Scope.UNKNOWN not in (sa, sb):
        return GateResult(
            Verdict.INCOMPARABLE_SCOPE, "scope_mismatch",
            f"Reported on different bases ({sa.value} vs {sb.value}); both figures "
            f"can be correct for the same period.",
            qualifier_diff=qdiff,
        )

    # --- issuer: two institutions projecting different values for the same
    # period are disagreeing forecasts, not a factual error — short-circuit
    # to INCOMPARABLE_ISSUER. But two institutions each ASSERTING a value as
    # historical fact are a genuine contradiction candidate; do NOT
    # short-circuit that case, let it fall through to the normal value
    # comparison. `cross_issuer` is still recorded either way so adjudicate()
    # can name both issuers in its explanation.
    ia, ib = a.qualifiers.issuer, b.qualifiers.issuer
    cross_issuer = bool(ia and ib and ia != ib)
    if cross_issuer and (a.modality in (Modality.ESTIMATED, Modality.PROJECTED)
                          or b.modality in (Modality.ESTIMATED, Modality.PROJECTED)):
        return GateResult(
            Verdict.INCOMPARABLE_ISSUER, "forecast_disagreement",
            f"{ia} and {ib} project different values for the same period; this is "
            f"forecast disagreement between institutions, not a factual error.",
            qualifier_diff=qdiff, cross_issuer=True,
        )

    # --- segment ------------------------------------------------------------
    seg_a, seg_b = a.qualifiers.segment, b.qualifiers.segment
    if seg_a and seg_b and seg_a != seg_b:
        return GateResult(
            Verdict.INCOMPARABLE_SEGMENT, "segment_mismatch",
            f"Different segments ({seg_a} vs {seg_b}).", qualifier_diff=qdiff,
            cross_issuer=cross_issuer,
        )

    # --- units --------------------------------------------------------------
    if isinstance(a.value, Quantity) and isinstance(b.value, Quantity):
        ok, why = _unit_compatible(a.value, b.value)
        if not ok:
            return GateResult(
                Verdict.INCOMPARABLE_UNIT, "unit_mismatch",
                f"Not directly comparable: {why}.", qualifier_diff=qdiff,
                cross_issuer=cross_issuer,
            )

    # --- period -------------------------------------------------------------
    rel = compare_periods(a.qualifiers.period, b.qualifiers.period)

    if rel in (PeriodRelation.SUCCEEDS, PeriodRelation.PRECEDES):
        return GateResult(
            Verdict.TEMPORAL_SUCCESSION, "temporal_succession",
            "These are point-in-time claims made at different dates; the later "
            "statement updates rather than contradicts the earlier one.",
            rel, qdiff, cross_issuer=cross_issuer,
        )

    if rel == PeriodRelation.DISJOINT:
        return GateResult(
            Verdict.INCOMPARABLE_PERIOD, "period_disjoint",
            f"Different reporting periods "
            f"({a.qualifiers.period.label!r} vs {b.qualifiers.period.label!r}); "
            f"the figures measure different windows of time.",
            rel, qdiff, cross_issuer=cross_issuer,
        )

    if rel in (PeriodRelation.SUBSUMES, PeriodRelation.SUBSUMED_BY):
        return GateResult(
            Verdict.AGGREGATION_CANDIDATE, "period_subsumption",
            "One period contains the other, so the smaller figure is expected to "
            "be a component of the larger, not equal to it.",
            rel, qdiff, cross_issuer=cross_issuer,
        )

    if rel == PeriodRelation.OVERLAPS:
        return GateResult(
            Verdict.INCOMPARABLE_PERIOD, "period_overlap",
            "Periods partially overlap; the figures are not on a like-for-like basis.",
            rel, qdiff, cross_issuer=cross_issuer,
        )

    # --- basis (audited vs unaudited) is a soft signal, not a blocker --------
    ba, bb = a.qualifiers.basis, b.qualifiers.basis
    if ba and bb and ba != bb and {ba, bb} == {"audited", "unaudited"}:
        return GateResult(
            Verdict.INCOMPARABLE_BASIS, "basis_mismatch",
            "One figure is audited and the other unaudited; a revision between "
            "them is expected rather than contradictory.",
            rel, qdiff, cross_issuer=cross_issuer,
        )

    return GateResult(Verdict.COMPARABLE, "comparable",
                      "Same subject, measure, scope, unit and period.", rel, qdiff,
                      cross_issuer=cross_issuer)
