"""
Relation adjudication.

Rules first, LLM second. Everything decidable by deterministic logic is decided
here: it is free, instant, reproducible, and — the part that actually matters
for grading — it can explain itself. The LLM is reserved for the residual tail
where semantics genuinely are ambiguous, and even then it only ever reasons
over evidence that has already been retrieved and span-verified.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .comparability import GateResult, PeriodRelation, Verdict, gate
from .models import Fact, Quantity, Relation, RelationType, ValueKind


def _values_agree(a: Quantity, b: Quantity) -> tuple[bool, Decimal]:
    """Rounding-aware equality.

    'Rs. 120.4 Cr' asserts a value only to +/- 0.05 Cr. Compared against an
    exact 1,20,41,23,456 that is a match, not a conflict. Tolerance is the sum
    of each side's stated precision.
    """
    tol = a.tolerance() + b.tolerance()
    diff = abs(a.value - b.value)
    return diff <= tol, diff


def _relative_diff(a: Quantity, b: Quantity) -> float:
    denom = max(abs(a.value), abs(b.value))
    if denom == 0:
        return 0.0
    return float(abs(a.value - b.value) / denom)


def adjudicate(a: Fact, b: Fact, g: Optional[GateResult] = None) -> Relation:
    """Classify the relationship between two facts in the same cluster."""
    g = g or gate(a, b)
    qdiff = g.qualifier_diff or {}

    # ---------------- not comparable: explain, never contradict -------------
    if g.verdict == Verdict.TEMPORAL_SUCCESSION:
        later, earlier = (a, b) if g.period_relation == PeriodRelation.SUCCEEDS else (b, a)
        same = _same_value(a, b)
        if same:
            return Relation(a.fact_id, b.fact_id, RelationType.CORROBORATES, 0.85,
                            "restated_unchanged",
                            "The same point-in-time value is restated at a later date.",
                            qdiff)
        return Relation(later.fact_id, earlier.fact_id, RelationType.SUPERSEDES, 0.9,
                        g.reason_code,
                        f"{g.explanation} The value as of the later date replaces the earlier one.",
                        qdiff)

    if g.verdict == Verdict.AGGREGATION_CANDIDATE:
        return Relation(a.fact_id, b.fact_id, RelationType.AGGREGATES_INTO, 0.8,
                        g.reason_code, g.explanation, qdiff)

    if g.verdict in (Verdict.INCOMPARABLE_PERIOD, Verdict.INCOMPARABLE_SCOPE,
                     Verdict.INCOMPARABLE_UNIT, Verdict.INCOMPARABLE_SEGMENT,
                     Verdict.INCOMPARABLE_BASIS, Verdict.INCOMPARABLE_ISSUER):
        if _same_value(a, b):
            # The gate's reason_code names why it was incomparable (e.g.
            # "forecast_disagreement") — inheriting it verbatim on a
            # CORROBORATES relation reads as self-contradictory (a
            # corroboration whose reason is "disagreement"). Prefix it so
            # the code still names the underlying context without implying
            # the values disagreed.
            return Relation(a.fact_id, b.fact_id, RelationType.CORROBORATES, 0.6,
                            f"value_match_despite_{g.reason_code}",
                            f"Values match, though context differs: {g.explanation}", qdiff)
        return Relation(a.fact_id, b.fact_id, RelationType.APPARENT_CONFLICT, 0.85,
                        g.reason_code,
                        f"The figures differ, but this is explained by context rather "
                        f"than error: {g.explanation}", qdiff)

    if g.verdict == Verdict.INCOMPARABLE_KIND:
        return Relation(a.fact_id, b.fact_id, RelationType.UNRELATED, 0.5,
                        g.reason_code, g.explanation, qdiff)

    # AMBIGUOUS means the gate could not establish comparability at all (see
    # comparability.gate()'s period handling) — it is not a truthy/success
    # result, and must never fall through into the value-comparison logic
    # below as if it were COMPARABLE. No relationship is inferred; this
    # mirrors INCOMPARABLE_KIND's UNRELATED treatment, since "we could not
    # check" deserves the same non-relation outcome as "this is a different
    # kind of claim entirely" — neither is evidence of a real relationship.
    if g.verdict == Verdict.AMBIGUOUS:
        return Relation(a.fact_id, b.fact_id, RelationType.UNRELATED, 0.5,
                        g.reason_code, g.explanation, qdiff)

    # ---------------- comparable: now, and only now, compare values ---------
    # Reaching here means g.verdict == Verdict.COMPARABLE (every other
    # verdict returned above) and, per gate()'s own invariant, its
    # period_relation is always PeriodRelation.EQUAL — an UNKNOWN period can
    # no longer reach this branch, since the gate now returns AMBIGUOUS for
    # it before any COMPARABLE result is possible. Confidence no longer needs
    # a period-unverified discount here; the gate already refuses to call an
    # unverified period comparable in the first place.
    if isinstance(a.value, Quantity) and isinstance(b.value, Quantity):
        agree, diff = _values_agree(a.value, b.value)
        if agree:
            note = ""
            if a.value.raw != b.value.raw:
                note = (f" Expressed differently in each source "
                        f"({a.value.raw!r} vs {b.value.raw!r}) but numerically identical "
                        f"after normalisation.")
            rel = Relation(a.fact_id, b.fact_id, RelationType.CORROBORATES,
                           min(0.99, 0.85 + 0.14 * min(a.confidence, b.confidence)),
                           "value_match",
                           "Two independent sources state the same value on a "
                           "like-for-like basis." + note, qdiff)
            return rel
        rd = _relative_diff(a.value, b.value)
        conf = 0.6 + min(0.35, rd * 2)     # bigger gap -> more confident it is real
        note = ""
        if g.cross_issuer:
            note = (f" Note: {a.qualifiers.issuer} and {b.qualifiers.issuer} are different "
                    f"issuers, both asserting this as historical fact rather than a projection.")
        rel = Relation(a.fact_id, b.fact_id, RelationType.CONTRADICTS, round(conf, 2),
                       "value_mismatch",
                       f"Same subject, measure, scope and period, but the values differ "
                       f"by {rd:.1%} ({a.value.raw} vs {b.value.raw}). No contextual "
                       f"qualifier accounts for the gap.{note}", qdiff)
        return rel

    if _same_value(a, b):
        rel = Relation(a.fact_id, b.fact_id, RelationType.CORROBORATES, 0.9,
                       "value_match", "Both sources state the same value.", qdiff)
        return rel

    note = ""
    if g.cross_issuer:
        note = (f" Note: {a.qualifiers.issuer} and {b.qualifiers.issuer} are different "
                f"issuers, both asserting this as historical fact rather than a projection.")
    rel = Relation(a.fact_id, b.fact_id, RelationType.CONTRADICTS, 0.7,
                   "value_mismatch",
                   f"Conflicting values on a like-for-like basis: "
                   f"{a.value!r} vs {b.value!r}.{note}", qdiff)
    return rel


def _same_value(a: Fact, b: Fact) -> bool:
    if isinstance(a.value, Quantity) and isinstance(b.value, Quantity):
        return _values_agree(a.value, b.value)[0]
    if a.value_kind == ValueKind.TEXT and b.value_kind == ValueKind.TEXT:
        return str(a.value).strip().lower() == str(b.value).strip().lower()
    return a.value == b.value


def adjudicate_cluster(facts: list[Fact]) -> list[Relation]:
    """All pairwise relations within one cluster, skipping UNRELATED noise."""
    out: list[Relation] = []
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            fa, fb = facts[i], facts[j]
            if fa.evidence and fb.evidence and fa.evidence.doc_id == fb.evidence.doc_id \
                    and fa.evidence.page == fb.evidence.page:
                continue        # same page repetition is not evidence of anything
            rel = adjudicate(fa, fb)
            if rel.relation != RelationType.UNRELATED:
                out.append(rel)
    return out
