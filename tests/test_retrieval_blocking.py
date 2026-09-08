"""Tests for fact_layer/retrieval/blocking.py.

Two groups: things blocking DOES eliminate (subject/measure/value_kind
mismatch — the exact set `gate()` turns into `UNRELATED`), and — just as
important for this architecture — things it must NEVER eliminate
(scope/segment/geography/basis/issuer mismatch, and unit/currency
mismatch of any kind), because `comparability.gate()` turns those into
real CORROBORATES/APPARENT_CONFLICT relations. Unit/currency mismatch
used to be blocked here too, on the assumption it was a narrow edge case;
measuring it against the real corpus proved that assumption wrong (it
silently discarded 8 of 15 real relations — see blocking.py's module
docstring), so it was removed. See blocking.py's module docstring for the
full reasoning.
"""

import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Scope, ValueKind,
)
from fact_layer.retrieval.blocking import block_key, block_key_fields, blocking_check


def _mkfact(subject="delhivery", measure="revenue", value_kind=ValueKind.QUANTITY,
            value=None, scope=Scope.UNKNOWN, segment=None, geography=None,
            unit="currency", currency="INR", period=None, issuer=None) -> Fact:
    if value is None:
        value = Quantity(value=Decimal("100"), unit=unit, currency=currency, sig_figs=3, raw="100")
    if period is None:
        period = Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label="FY2023-24")
    return Fact(
        subject=subject, measure=measure, value_kind=value_kind, value=value,
        qualifiers=Qualifiers(
            period=period,
            scope=scope, segment=segment, geography=geography, issuer=issuer,
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


# --------------------------------------------------------------------------
# NOT blocked — must reach gate() so CORROBORATES/APPARENT_CONFLICT stay
# discoverable (task section 22 / this project's "Case 3" capability).
# --------------------------------------------------------------------------

def test_different_unit_category_not_blocked():
    # Real-corpus regression: this used to be blocked as
    # "UNIT_CATEGORY_MISMATCH" on the assumption it was a rare edge case.
    # Measured against data/store.json, that rule silently discarded 8 of
    # the corpus's 15 real relations (gate() reaches INCOMPARABLE_UNIT ->
    # a real APPARENT_CONFLICT/CORROBORATES relation for exactly this
    # shape whenever scope/segment/issuer already matched) — see
    # blocking.py's module docstring.
    a = _mkfact(unit="percent", currency=None)
    b = _mkfact(unit="currency", currency="INR")
    assert blocking_check(a, b).candidate is True

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

# --------------------------------------------------------------------------
# block_key equivalence — the property the whole scale argument rests on
# --------------------------------------------------------------------------

_OTHER_PERIOD = Period(PeriodKind.DURATION, date(2021, 4, 1), date(2022, 3, 31), label="FY2021-22")


def test_block_key_equality_is_exactly_blocking_check():
    """`blocking_check(a, b).candidate` IS `block_key_fields(a) == block_key_fields(b)`.

    This is not a nice-to-have. Retrieval applies blocking as a SEARCH
    RESTRICTION (each query only scans its own bucket) rather than as a
    post-retrieval filter, and that is lossless ONLY while this equivalence
    holds. If a field is added to `block_key_fields()` that
    `blocking_check()` does not compare — or vice versa — retrieval starts
    silently dropping real candidates, which is exactly the failure mode
    the 8/15 unit-category regression taught this project to fear. Pinned
    exhaustively over a matrix spanning every dimension, blocked and
    unblocked alike."""
    facts = []
    for subj in ("delhivery", "rbi"):
        for meas in ("revenue", "ebitda"):
            for kind in (ValueKind.QUANTITY, ValueKind.TEXT):
                for per in (None, _OTHER_PERIOD):
                    for sc in (Scope.UNKNOWN, Scope.STANDALONE, Scope.CONSOLIDATED):
                        for unit, cur in (("currency", "INR"), ("currency", "USD"), ("percent", None)):
                            value = (Quantity(value=Decimal("100"), unit=unit, currency=cur,
                                              sig_figs=3, raw="100")
                                     if kind == ValueKind.QUANTITY else "some text")
                            facts.append(_mkfact(
                                subject=subj, measure=meas, value_kind=kind, value=value,
                                period=per, scope=sc, unit=unit, currency=cur,
                            ))

    checked = 0
    for a in facts:
        for b in facts:
            checked += 1
            expected = block_key_fields(a) == block_key_fields(b)
            assert blocking_check(a, b).candidate == expected
            if expected:
                assert block_key(a) == block_key(b)
    assert checked > 10_000


def test_block_key_is_deterministic():
    f = _mkfact()
    assert block_key(f) == block_key(f)
    assert len(block_key(f)) == 16


def test_block_key_never_grows_to_include_contextual_dimensions():
    """A direct guard on the 8/15 regression: period, unit, currency,
    scope, segment, geography and issuer must NOT influence the bucket —
    they have to reach comparability.gate() to become real relations."""
    base = _mkfact()
    variants = {
        "period": _mkfact(period=_OTHER_PERIOD),
        "scope": _mkfact(scope=Scope.CONSOLIDATED),
        "currency": _mkfact(currency="USD"),
        "unit": _mkfact(unit="percent", currency=None),
        "segment": _mkfact(segment="express"),
        "geography": _mkfact(geography="india"),
        "issuer": _mkfact(issuer="RBI"),
    }
    for name, v in variants.items():
        assert block_key(base) == block_key(v), (
            f"{name} leaked into the blocking bucket — this silently deletes "
            "real relations (see blocking.py's 8/15 measurement)"
        )
        assert blocking_check(base, v).candidate is True
