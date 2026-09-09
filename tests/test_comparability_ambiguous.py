"""
Gate-level regression tests for the AMBIGUOUS-period fix.

`comparability.gate()` used to let `PeriodRelation.UNKNOWN` fall through
every explicit period branch and reach the terminal `return
GateResult(Verdict.COMPARABLE, ...)`, whose explanation claimed "Same
subject, measure, scope, unit and period" — an authoritative overclaim the
gate had never actually verified. Downstream, `adjudicate.py` had grown a
confidence-halving patch (`_flag_period_unverified`, citing "Project
specification section 17") that treated the symptom without fixing the
cause. This file pins the source-of-truth fix instead: `PeriodRelation.
UNKNOWN` now returns `Verdict.AMBIGUOUS` with reason_code
"ambiguous_period", before any COMPARABLE result is reachable, and
`adjudicate()` refuses to treat AMBIGUOUS as a truthy/success verdict.

Covers the required scenarios:
  1. unknown period + known period -> AMBIGUOUS
  2. unknown period + unknown period -> AMBIGUOUS
  3. different known periods -> INCOMPARABLE
  4. same known periods -> COMPARABLE
  5. no AMBIGUOUS result may contain a COMPARABLE success explanation
  6. audit all other dimensions for the same UNKNOWN-fallthrough pattern
  7. existing known comparability cases remain unchanged
  8. relationship adjudication does not run for AMBIGUOUS gate results
"""

import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.adjudicate import adjudicate
from fact_layer.comparability import PeriodRelation, Verdict, compare_periods, gate
from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, RelationType, Scope, ValueKind,
)
from fact_layer.normalize import parse_period


def ev(doc="d1", page=1, quote="100"):
    return Evidence(doc_id=doc, page=page, char_start=0, char_end=len(quote),
                    verbatim_quote=quote, verified=True)


def qty(val="100", unit="currency", currency="INR"):
    return Quantity(value=Decimal(val), unit=unit, currency=currency, sig_figs=4, raw=val)


def fact(period=None, scope=Scope.UNKNOWN, val="100", subject="acme", measure="revenue",
         segment=None, basis=None, issuer=None, doc="d1") -> Fact:
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY, value=qty(val),
        qualifiers=Qualifiers(period=period, scope=scope, segment=segment, basis=basis, issuer=issuer),
        evidence=ev(doc, quote=val),
    )


FY2324 = parse_period("FY2023-24")   # 2023-04-01 .. 2024-03-31, a real parsed period
FY2122 = parse_period("FY2021-22")   # a different, disjoint real period
UNPARSEABLE = Period(PeriodKind.DURATION, start=None, end=None, label="the reporting period")


# --------------------------------------------------------------------------
# 1. unknown period + known period -> AMBIGUOUS
# --------------------------------------------------------------------------

def test_unstated_period_vs_known_period_is_ambiguous():
    a = fact(period=FY2324)
    b = fact(period=None)
    g = gate(a, b)
    assert g.verdict == Verdict.AMBIGUOUS
    assert g.reason_code == "ambiguous_period"
    assert g.period_relation == PeriodRelation.UNKNOWN


def test_unparseable_period_vs_known_period_is_ambiguous():
    """A period object exists (a label was extracted) but its date could not
    be parsed — a different situation from period=None, and the gate must
    treat it the same way: unresolved, not comparable."""
    a = fact(period=FY2324)
    b = fact(period=UNPARSEABLE)
    g = gate(a, b)
    assert g.verdict == Verdict.AMBIGUOUS
    assert g.reason_code == "ambiguous_period"


# --------------------------------------------------------------------------
# 2. unknown period + unknown period -> AMBIGUOUS
# --------------------------------------------------------------------------

def test_both_periods_unstated_is_ambiguous():
    a = fact(period=None)
    b = fact(period=None)
    g = gate(a, b)
    assert g.verdict == Verdict.AMBIGUOUS
    assert g.reason_code == "ambiguous_period"


def test_both_periods_unparseable_is_ambiguous():
    a = fact(period=UNPARSEABLE)
    b = fact(period=Period(PeriodKind.DURATION, start=None, end=None, label="a later period"))
    g = gate(a, b)
    assert g.verdict == Verdict.AMBIGUOUS
    assert g.reason_code == "ambiguous_period"


# --------------------------------------------------------------------------
# 3. different known periods -> INCOMPARABLE (unchanged; not this bug)
# --------------------------------------------------------------------------

def test_different_known_disjoint_periods_is_incomparable():
    a = fact(period=FY2324)
    b = fact(period=FY2122)
    g = gate(a, b)
    assert compare_periods(FY2324, FY2122) == PeriodRelation.DISJOINT
    assert g.verdict == Verdict.INCOMPARABLE_PERIOD
    assert g.reason_code == "period_disjoint"
    assert g.verdict != Verdict.AMBIGUOUS


# --------------------------------------------------------------------------
# 4. same known periods -> COMPARABLE
# --------------------------------------------------------------------------

def test_same_known_periods_is_comparable():
    a = fact(period=FY2324)
    b = fact(period=parse_period("FY2023-24"))   # independently parsed, same window
    g = gate(a, b)
    assert g.verdict == Verdict.COMPARABLE
    assert g.period_relation == PeriodRelation.EQUAL
    assert "period" in g.explanation.lower()


# --------------------------------------------------------------------------
# 5. no AMBIGUOUS result may contain a COMPARABLE success explanation
# --------------------------------------------------------------------------

def test_ambiguous_explanation_never_claims_same_period():
    for a, b in [
        (fact(period=FY2324), fact(period=None)),
        (fact(period=None), fact(period=None)),
        (fact(period=FY2324), fact(period=UNPARSEABLE)),
    ]:
        g = gate(a, b)
        assert g.verdict == Verdict.AMBIGUOUS
        low = g.explanation.lower()
        # The COMPARABLE success phrase must never appear on an AMBIGUOUS
        # result. The explanation is allowed to discuss "the same" as part of
        # naming the uncertainty ("cannot establish whether ... the same"),
        # so this checks the specific overclaiming sentence, not the word.
        assert "same subject, measure, scope, unit and period" not in low
        assert not low.startswith("same ")
        assert any(word in low for word in ("cannot", "unresolved", "unverified"))


def test_comparable_period_claim_is_only_reachable_when_period_actually_equal():
    """The terminal COMPARABLE explanation names 'period' — assert this is
    only ever produced when the gate's own period_relation is EQUAL, i.e.
    actually verified, never UNKNOWN."""
    a = fact(period=FY2324)
    b = fact(period=parse_period("FY2023-24"))
    g = gate(a, b)
    assert g.verdict == Verdict.COMPARABLE
    assert "period" in g.explanation.lower()
    assert g.period_relation == PeriodRelation.EQUAL


# --------------------------------------------------------------------------
# 6. audit all other dimensions for the same UNKNOWN-fallthrough pattern
# --------------------------------------------------------------------------
# Scope, segment, basis and issuer each have their own "unstated" sentinel
# (Scope.UNKNOWN, or None) and are all intentionally left permissive — see
# comparability.py's UNKNOWN-fallthrough audit note above _unit_compatible.
# These tests pin that this is a *documented policy*, not an unaudited gap:
# the verdict stays COMPARABLE, but the explanation must not claim "same
# scope" when scope was never actually verified as equal.

def test_unknown_scope_on_either_side_remains_comparable_by_policy():
    a = fact(period=FY2324, scope=Scope.STANDALONE)
    b = fact(period=FY2324, scope=Scope.UNKNOWN)
    g = gate(a, b)
    assert g.verdict == Verdict.COMPARABLE   # unchanged: intentional policy
    # The verdict stays permissive, but the wording must not claim scope was
    # verified as equal — the specific overclaiming phrase must be absent,
    # even though "scope" itself is still named (to explain the caveat).
    assert "same subject, measure, scope, unit and period" not in g.explanation.lower()


def test_known_scope_mismatch_still_blocks():
    """Confirms the scope policy is 'permissive on unknown', not 'permissive
    on mismatch' — a genuine standalone vs consolidated clash still blocks,
    unaffected by this fix."""
    a = fact(period=FY2324, scope=Scope.STANDALONE)
    b = fact(period=FY2324, scope=Scope.CONSOLIDATED)
    g = gate(a, b)
    assert g.verdict == Verdict.INCOMPARABLE_SCOPE


def test_unknown_segment_basis_issuer_remain_comparable_by_policy():
    """None on either side of segment/basis/issuer never blocks — gate()
    only compares them when BOTH sides state a value (see comparability.py's
    audit note). This is unaffected by the period fix and asserted here so a
    future change to that policy has to touch this test deliberately."""
    a = fact(period=FY2324, segment="Retail", basis="audited", issuer="RBI")
    b = fact(period=FY2324, segment=None, basis=None, issuer=None)
    g = gate(a, b)
    assert g.verdict == Verdict.COMPARABLE


def test_value_kind_and_unit_have_no_unknown_sentinel():
    """value_kind is always determinate (no UNKNOWN member) and Quantity.unit
    defaults to a concrete string, never a sentinel — so there is no
    fallthrough pattern to audit for either; both already block correctly
    on an actual mismatch."""
    a = Fact(subject="x", measure="m", value_kind=ValueKind.QUANTITY, value=qty(),
             qualifiers=Qualifiers(period=FY2324), evidence=ev())
    b = Fact(subject="x", measure="m", value_kind=ValueKind.TEXT, value="some text",
             qualifiers=Qualifiers(period=FY2324), evidence=ev())
    assert gate(a, b).verdict == Verdict.INCOMPARABLE_KIND


# --------------------------------------------------------------------------
# 7. existing known comparability cases remain unchanged
# --------------------------------------------------------------------------

def test_temporal_succession_unaffected():
    a = Fact(subject="x", measure="status", value_kind=ValueKind.BOOLEAN, value=True,
             qualifiers=Qualifiers(period=Period(PeriodKind.INSTANT, date(2024, 3, 31), date(2024, 3, 31))),
             evidence=ev())
    b = Fact(subject="x", measure="status", value_kind=ValueKind.BOOLEAN, value=False,
             qualifiers=Qualifiers(period=Period(PeriodKind.INSTANT, date(2025, 3, 12), date(2025, 3, 12))),
             evidence=ev())
    g = gate(a, b)
    assert g.verdict == Verdict.TEMPORAL_SUCCESSION


def test_aggregation_candidate_unaffected():
    fy = parse_period("FY2023-24")
    q1 = parse_period("Q1 FY2023-24")
    a = fact(period=fy)
    b = fact(period=q1)
    g = gate(a, b)
    assert g.verdict == Verdict.AGGREGATION_CANDIDATE


def test_unit_mismatch_unaffected():
    a = fact(period=FY2324, val="100")
    b = Fact(subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
             value=qty(val="100", unit="currency", currency="USD"),
             qualifiers=Qualifiers(period=FY2324), evidence=ev())
    g = gate(a, b)
    assert g.verdict == Verdict.INCOMPARABLE_UNIT


def test_forecast_disagreement_unaffected():
    from fact_layer.models import Modality
    a = fact(period=FY2324, issuer="IMF")
    b = fact(period=FY2324, issuer="RBI")
    a.modality = Modality.PROJECTED
    b.modality = Modality.PROJECTED
    g = gate(a, b)
    assert g.verdict == Verdict.INCOMPARABLE_ISSUER
    assert g.reason_code == "forecast_disagreement"


# --------------------------------------------------------------------------
# 8. relationship adjudication does not run for AMBIGUOUS gate results
# --------------------------------------------------------------------------

def test_adjudicate_never_returns_a_comparing_relation_for_ambiguous_period():
    for a, b in [
        (fact(period=FY2324), fact(period=None)),
        (fact(period=None), fact(period=None)),
        (fact(period=FY2324), fact(period=UNPARSEABLE)),
    ]:
        g = gate(a, b)
        assert g.verdict == Verdict.AMBIGUOUS
        rel = adjudicate(a, b, g)
        assert rel.relation == RelationType.UNRELATED
        assert rel.relation not in (RelationType.CORROBORATES, RelationType.CONTRADICTS)
        assert rel.reason_code == "ambiguous_period"


def test_adjudicate_recomputes_gate_itself_and_still_blocks_ambiguous():
    """adjudicate() also calls gate() internally when not given a
    precomputed GateResult — confirms the guard holds on that path too, not
    only when a caller passes g explicitly."""
    a = fact(period=FY2324)
    b = fact(period=None)
    rel = adjudicate(a, b)   # no g= passed
    assert rel.relation == RelationType.UNRELATED
    assert rel.reason_code == "ambiguous_period"


def test_adjudicate_cluster_drops_ambiguous_pairs_as_unrelated_noise():
    facts = [fact(period=FY2324, val="100"), fact(period=None, val="100")]
    from fact_layer.adjudicate import adjudicate_cluster
    relations = adjudicate_cluster(facts)
    # UNRELATED is filtered out of adjudicate_cluster's output by design —
    # an ambiguous pair must produce no stored relation at all.
    assert relations == []
