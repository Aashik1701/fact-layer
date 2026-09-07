"""Regression tests for Fact.compute_id() (correctness-hardening milestone).

The project specification documented a known collision: compute_id() hashed
subject/measure/value/doc_id/page/char_start but not qualifiers, value_kind,
or modality, so two materially different facts anchored at the same evidence
span collided on one id (~0.3% loss, 688 -> 686 facts in the full corpus).
These tests pin down the fixed identity rule: same claim + same qualifiers +
same anchor -> same id; any identity-relevant difference -> a different id.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence,
    Fact,
    Modality,
    Period,
    PeriodKind,
    Qualifiers,
    Quantity,
    Scope,
    ValueKind,
)
from decimal import Decimal


def _ev(doc_id="d1", page=1, char_start=0, quote="120.4"):
    return Evidence(doc_id=doc_id, page=page, char_start=char_start,
                     char_end=char_start + len(quote), verbatim_quote=quote, verified=True)


def _qty(v="120.4"):
    return Quantity(value=Decimal(v), unit="currency", currency="INR", sig_figs=4, raw=v)


def _base_fact(**overrides) -> Fact:
    kwargs = dict(
        subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
        value=_qty(), qualifiers=Qualifiers(scope=Scope.STANDALONE),
        modality=Modality.ASSERTED, evidence=_ev(),
    )
    kwargs.update(overrides)
    return Fact(**kwargs)


# --- TEST 1: identical inputs -> identical id -----------------------------
def test_same_fact_same_qualifiers_same_anchor_same_id():
    a = _base_fact()
    b = _base_fact()
    assert a.fact_id == b.fact_id


# --- TEST 2: different period -> different id ------------------------------
def test_different_period_different_id():
    p1 = Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label="FY2023-24")
    p2 = Period(PeriodKind.DURATION, date(2024, 4, 1), date(2025, 3, 31), label="FY2024-25")
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, period=p1))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, period=p2))
    assert a.fact_id != b.fact_id


# --- TEST 3: different scope -> different id -------------------------------
def test_different_scope_different_id():
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.CONSOLIDATED))
    assert a.fact_id != b.fact_id


# --- TEST 4: different modality -> different id ----------------------------
def test_different_modality_different_id():
    a = _base_fact(modality=Modality.ASSERTED)
    b = _base_fact(modality=Modality.PROJECTED)
    assert a.fact_id != b.fact_id


# --- TEST 5: different issuer -> different id ------------------------------
def test_different_issuer_different_id():
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, issuer="RBI"))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, issuer="IMF"))
    assert a.fact_id != b.fact_id


# --- TEST 6: None vs populated qualifier -> distinct ids -------------------
def test_none_issuer_vs_populated_issuer_different_id():
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, issuer=None))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, issuer="RBI"))
    assert a.fact_id != b.fact_id


def test_none_period_vs_populated_period_different_id():
    p = Period(PeriodKind.INSTANT, date(2024, 3, 31), date(2024, 3, 31), label="as on 31 March 2024")
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, period=None))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, period=p))
    assert a.fact_id != b.fact_id


def test_none_basis_vs_empty_string_basis_are_distinguished():
    """None and '' must not collapse to the same identity slot even though
    both are 'falsy' — json.dumps(None) -> null, json.dumps('') -> "" so the
    canonical seed already keeps them apart; this pins that behaviour down."""
    a = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, basis=None))
    b = _base_fact(qualifiers=Qualifiers(scope=Scope.STANDALONE, basis=""))
    assert a.fact_id != b.fact_id


# --- TEST: different value_kind -> different id ----------------------------
def test_different_value_kind_different_id():
    a = _base_fact(value_kind=ValueKind.QUANTITY, value=_qty())
    b = _base_fact(value_kind=ValueKind.TEXT, value="120.4")
    assert a.fact_id != b.fact_id


# --- TEST: same claim/qualifiers, different evidence anchor -> different id
def test_different_char_start_different_id():
    a = _base_fact(evidence=_ev(char_start=0))
    b = _base_fact(evidence=_ev(char_start=42))
    assert a.fact_id != b.fact_id


# --- TEST 7: semantically identical normalised values -> stable id --------
def test_semantically_equal_quantities_with_identical_repr_give_stable_id():
    """Two facts built from the SAME already-normalised Quantity (same
    Decimal, unit, currency, sig_figs, raw) at the same anchor must always
    produce the same id — determinism is not accidental."""
    q = _qty()
    a = Fact(subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
             value=Quantity(value=q.value, unit=q.unit, currency=q.currency,
                            sig_figs=q.sig_figs, raw=q.raw),
             qualifiers=Qualifiers(scope=Scope.STANDALONE), evidence=_ev())
    b = Fact(subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
             value=Quantity(value=q.value, unit=q.unit, currency=q.currency,
                            sig_figs=q.sig_figs, raw=q.raw),
             qualifiers=Qualifiers(scope=Scope.STANDALONE), evidence=_ev())
    assert a.fact_id == b.fact_id


# --- TEST 8: compute_id() is a pure, repeatable function -------------------
def test_compute_id_is_repeatable_when_called_directly():
    a = _base_fact()
    ids = {a.compute_id() for _ in range(20)}
    assert len(ids) == 1
    assert a.fact_id in ids


# --- the exact collision scenario previously observed ----------------------
def test_the_documented_collision_scenario_is_now_resolved():
    """Previously observed collision: two facts at the same span (same subject,
    measure, value, doc_id, page, char_start) differing only in a qualifier
    used to collide on one id. This is that exact scenario, now fixed."""
    shared_evidence = _ev(doc_id="delhivery_annual_report", page=43, char_start=2472,
                          quote="No. of ESOPs vested as on - 676,000 - 250,000")
    q = _qty("676000")
    plan_ii = Fact(subject="esops", measure="no_of_esops_vested", value_kind=ValueKind.QUANTITY,
                   value=q, qualifiers=Qualifiers(segment="ESOP Plan II"), evidence=shared_evidence)
    plan_iv = Fact(subject="esops", measure="no_of_esops_vested", value_kind=ValueKind.QUANTITY,
                   value=q, qualifiers=Qualifiers(segment="ESOP Plan IV"), evidence=shared_evidence)
    assert plan_ii.fact_id != plan_iv.fact_id, \
        "two facts at the same span differing only in a qualifier must no longer collide"


def test_value_verification_field_excluded_from_identity():
    """value_verification is an audit annotation, not part of the claim —
    two otherwise-identical facts that merely differ in that field (e.g. one
    evaluated by an older/newer verifier run) must still be the SAME fact."""
    a = _base_fact(value_verification="verified", value_verification_reason="value_matches_sole_quote_number")
    b = _base_fact(value_verification="", value_verification_reason="")
    assert a.fact_id == b.fact_id
