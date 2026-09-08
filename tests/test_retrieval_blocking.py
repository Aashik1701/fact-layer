"""Tests for fact_layer/retrieval/blocking.py.

Two groups: things blocking DOES eliminate (subject/measure/value_kind/
unit-category mismatch), and — just as important for this architecture —
things it must NEVER eliminate (scope/segment/geography/basis/issuer
mismatch, and same-category currency mismatch), because
`comparability.gate()` turns those into real CORROBORATES/APPARENT_CONFLICT
relations. See blocking.py's module docstring for the full reasoning.
"""

import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Scope, ValueKind,
)
from fact_layer.retrieval.blocking import blocking_check


def _mkfact(subject="delhivery", measure="revenue", value_kind=ValueKind.QUANTITY,
            value=None, scope=Scope.UNKNOWN, segment=None, geography=None,
            unit="currency", currency="INR") -> Fact:
    if value is None:
        value = Quantity(value=Decimal("100"), unit=unit, currency=currency, sig_figs=3, raw="100")
    return Fact(
        subject=subject, measure=measure, value_kind=value_kind, value=value,
        qualifiers=Qualifiers(
            period=Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label="FY2023-24"),
            scope=scope, segment=segment, geography=geography,
        ),
        evidence=Evidence(doc_id="d1", page=1, char_start=0, char_end=3, verbatim_quote="100", verified=True),
        subject_raw=subject, measure_raw=measure,
    )


# --------------------------------------------------------------------------
# Blocked
# --------------------------------------------------------------------------

def test_same_subject_and_measure_is_a_candidate():
    a, b = _mkfact(), _mkfact()
    result = blocking_check(a, b)
    assert result.candidate is True
    assert result.reason is None


def test_different_subject_blocked():
    a, b = _mkfact(subject="delhivery"), _mkfact(subject="rbi")
    result = blocking_check(a, b)
    assert result.candidate is False
    assert result.reason == "SUBJECT_MISMATCH"


def test_different_measure_blocked():
    a, b = _mkfact(measure="revenue"), _mkfact(measure="ebitda")
    result = blocking_check(a, b)
    assert result.candidate is False
    assert result.reason == "MEASURE_MISMATCH"


def test_different_value_kind_blocked():
    a = _mkfact(value_kind=ValueKind.QUANTITY)
    b = _mkfact(value_kind=ValueKind.TEXT, value="some text")
    result = blocking_check(a, b)
    assert result.candidate is False
    assert result.reason == "VALUE_KIND_MISMATCH"


def test_different_unit_category_blocked():
    a = _mkfact(unit="percent", currency=None)
    b = _mkfact(unit="currency", currency="INR")
    result = blocking_check(a, b)
    assert result.candidate is False
    assert result.reason == "UNIT_CATEGORY_MISMATCH"


# --------------------------------------------------------------------------
# NOT blocked — must reach gate() so CORROBORATES/APPARENT_CONFLICT stay
# discoverable (task section 22 / this project's "Case 3" capability).
# --------------------------------------------------------------------------

def test_scope_mismatch_not_blocked():
    a = _mkfact(scope=Scope.STANDALONE)
    b = _mkfact(scope=Scope.CONSOLIDATED)
    assert blocking_check(a, b).candidate is True


def test_segment_mismatch_not_blocked():
    a = _mkfact(segment="logistics")
    b = _mkfact(segment="express")
    assert blocking_check(a, b).candidate is True


def test_geography_mismatch_not_blocked():
    a = _mkfact(geography="India")
    b = _mkfact(geography="US")
    assert blocking_check(a, b).candidate is True


def test_same_category_different_currency_not_blocked():
    a = _mkfact(unit="currency", currency="INR")
    b = _mkfact(unit="currency", currency="USD")
    assert blocking_check(a, b).candidate is True
