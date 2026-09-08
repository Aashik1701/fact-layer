"""Tests for fact_layer/retrieval/text.py — deterministic retrieval text."""

import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import Evidence, Fact, Period, PeriodKind, Qualifiers, Quantity, Scope, ValueKind
from fact_layer.retrieval.text import fact_to_retrieval_text


def _mkfact(**overrides) -> Fact:
    defaults = dict(
        subject="delhivery",
        measure="revenue",
        value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal("1204000000"), unit="currency", currency="INR", sig_figs=4, raw="120.4 Cr"),
        qualifiers=Qualifiers(period=Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label="FY2023-24")),
        evidence=Evidence(doc_id="d1", page=1, char_start=0, char_end=5, verbatim_quote="120.4", verified=True),
        subject_raw="Delhivery", measure_raw="Revenue",
    )
    defaults.update(overrides)
    return Fact(**defaults)


def test_deterministic_for_identical_facts():
    f1 = _mkfact()
    f2 = _mkfact()
    assert fact_to_retrieval_text(f1) == fact_to_retrieval_text(f2)


def test_deterministic_across_repeated_calls():
    f = _mkfact()
    assert fact_to_retrieval_text(f) == fact_to_retrieval_text(f) == fact_to_retrieval_text(f)


def test_differs_when_period_differs():
    f1 = _mkfact()
    f2 = _mkfact(qualifiers=Qualifiers(period=Period(PeriodKind.DURATION, date(2024, 4, 1), date(2025, 3, 31), label="FY2024-25")))
    assert fact_to_retrieval_text(f1) != fact_to_retrieval_text(f2)


def test_never_includes_raw_evidence_quote():
    f = _mkfact(evidence=Evidence(doc_id="d1", page=1, char_start=0, char_end=5,
                                   verbatim_quote="A DISTINCTIVE_QUOTE_MARKER 120.4", verified=True))
    text = fact_to_retrieval_text(f)
    assert "DISTINCTIVE_QUOTE_MARKER" not in text


def test_includes_canonical_fields():
    f = _mkfact(qualifiers=Qualifiers(
        period=Period(PeriodKind.DURATION, date(2023, 4, 1), date(2024, 3, 31), label="FY2023-24"),
        scope=Scope.CONSOLIDATED, segment="logistics", geography="India", issuer="Delhivery",
    ))
    text = fact_to_retrieval_text(f)
    for expected in ("subject: delhivery", "measure: revenue", "scope: consolidated",
                     "segment: logistics", "geography: India", "issuer: Delhivery", "FY2023-24"):
        assert expected in text


def test_extra_qualifiers_included_deterministically():
    f1 = _mkfact(qualifiers=Qualifiers(extra={"b": "2", "a": "1"}))
    f2 = _mkfact(qualifiers=Qualifiers(extra={"a": "1", "b": "2"}))
    assert fact_to_retrieval_text(f1) == fact_to_retrieval_text(f2)
    assert "extra.a: 1" in fact_to_retrieval_text(f1)
