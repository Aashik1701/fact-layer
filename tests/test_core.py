"""Tests for the six cases that separate this system from a naive numeric diff."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from fact_layer.models import (Evidence, Fact, Modality, Qualifiers, Scope, ValueKind)
from fact_layer.normalize import (address_similarity, normalize_entity, parse_period,
                                  parse_quantity, detect_scope)
from fact_layer.adjudicate import adjudicate
from fact_layer.comparability import Verdict, gate
from fact_layer.models import RelationType


def ev(doc, page=1, quote="x"):
    return Evidence(doc_id=doc, page=page, char_start=0, char_end=len(quote),
                    verbatim_quote=quote, verified=True)


def qfact(measure, raw, ctx, period_txt, doc, scope=Scope.UNKNOWN, page=1):
    q = parse_quantity(raw, ctx)
    return Fact(subject="acme technologies", measure=measure,
                value_kind=ValueKind.QUANTITY, value=q,
                qualifiers=Qualifiers(period=parse_period(period_txt), scope=scope),
                evidence=ev(doc, page, raw))


# --- 1. cross-period figures are NOT a contradiction ----------------------
def test_period_disjoint_is_apparent_conflict():
    a = qfact("revenue", "Rs. 120.4 Cr", "", "FY2023-24", "annual_report")
    b = qfact("revenue", "Rs. 32.1 Cr", "", "Q1 FY2024-25", "quarterly")
    r = adjudicate(a, b)
    assert r.relation == RelationType.APPARENT_CONFLICT
    assert r.reason_code == "period_disjoint"


# --- 2. standalone vs consolidated ---------------------------------------
def test_scope_mismatch_is_apparent_conflict():
    a = qfact("revenue", "Rs. 120.4 Cr", "", "FY2023-24", "d1", Scope.STANDALONE)
    b = qfact("revenue", "Rs. 145.9 Cr", "", "FY2023-24", "d2", Scope.CONSOLIDATED)
    g = gate(a, b)
    assert g.verdict == Verdict.INCOMPARABLE_SCOPE
    assert adjudicate(a, b).relation == RelationType.APPARENT_CONFLICT


# --- 3. lakh vs crore is the SAME number ---------------------------------
def test_indian_scale_corroborates():
    a = qfact("revenue", "12,040", "Rs. in lakhs", "FY2023-24", "d1")
    b = qfact("revenue", "Rs. 120.4 Cr", "", "FY2023-24", "d2")
    r = adjudicate(a, b)
    assert r.relation == RelationType.CORROBORATES
    assert a.value.value == b.value.value == 1_204_000_000


# --- 4. genuine contradiction --------------------------------------------
def test_genuine_contradiction():
    a = qfact("revenue", "Rs. 120.4 Cr", "", "FY2023-24", "d1", Scope.STANDALONE)
    b = qfact("revenue", "Rs. 131.2 Cr", "", "FY2023-24", "d2", Scope.STANDALONE)
    r = adjudicate(a, b)
    assert r.relation == RelationType.CONTRADICTS
    assert r.confidence > 0.6


# --- 5. director status: supersession, not contradiction ------------------
def test_director_supersession():
    a = Fact(subject="r krishnan", measure="directorship_status",
             value_kind=ValueKind.TEXT, value="active",
             qualifiers=Qualifiers(period=parse_period("as on 31.03.2024")),
             evidence=ev("annual_report_2024"))
    b = Fact(subject="r krishnan", measure="directorship_status",
             value_kind=ValueKind.TEXT, value="resigned",
             qualifiers=Qualifiers(period=parse_period("w.e.f. 12-03-2025")),
             evidence=ev("board_report_2025"))
    r = adjudicate(a, b)
    assert r.relation == RelationType.SUPERSEDES
    assert r.source_fact_id == b.fact_id      # later fact supersedes earlier


# --- 6. currency mismatch is flagged, never silently converted ------------
def test_currency_not_silently_converted():
    a = qfact("revenue", "Rs. 120.4 Cr", "", "FY2023-24", "d1")
    b = qfact("revenue", "US$ 14.5 million", "", "FY2023-24", "d2")
    assert gate(a, b).verdict == Verdict.INCOMPARABLE_UNIT


# --- normalisation spot checks -------------------------------------------
def test_entity_normalisation():
    assert normalize_entity("Acme Technologies Pvt. Ltd.") == \
           normalize_entity("ACME TECHNOLOGIES PRIVATE LIMITED")


def test_address_variants_match():
    a = "No. 12, 2nd Cross, Indiranagar, Bengaluru 560038"
    b = "12, II Cross Rd, Indira Nagar, Bangalore - 560 038"
    assert address_similarity(a, b) > 0.7


def test_fiscal_year_window():
    p = parse_period("FY2023-24")
    assert p.start == date(2023, 4, 1) and p.end == date(2024, 3, 31)


def test_quarter_window():
    p = parse_period("Q1 FY2024-25")
    assert p.start == date(2024, 4, 1) and p.end == date(2024, 6, 30)


def test_year_ended_phrase():
    p = parse_period("year ended 31 March 2024")
    assert p.start == date(2023, 4, 1) and p.end == date(2024, 3, 31)


def test_scope_detection():
    assert detect_scope("Consolidated Statement of Profit and Loss") == Scope.CONSOLIDATED
