"""Tests for the issuer qualifier (milestone 3.5) — see CLAUDE.md section 13.

Where the real M3 extraction output supports it, these tests use REAL facts
reconstructed via LLM_MODE=replay against the actual cache/llm/ committed
from that run (not synthetic dicts) — the real IMF/RBI GDP disagreement is
exactly the case this feature exists for. Two cases (temporal succession,
and the ASSERTED-vs-ASSERTED contrast) fall back to hand-built facts because
the real corpus doesn't happen to contain that exact shape (GDP projections
are duration periods, not point-in-time claims; and no ASSERTED RBI GDP
figure landed for the same period IMF asserted one for) — noted inline.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from fact_layer.adjudicate import adjudicate
from fact_layer.comparability import Verdict, gate
from fact_layer.extract import extract_document, extract_document_defaults
from fact_layer.models import (
    Evidence,
    Fact,
    Modality,
    Qualifiers,
    RelationType,
    ValueKind,
)
from fact_layer.normalize import parse_period, parse_quantity
from fact_layer.parse import parse_pdf
from fact_layer.triage import _DEMO_BUDGETS, default_budget, select_pages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RBI_PDF = os.path.join(ROOT, "starter-datasets", "india-macroeconomy", "02-rbi-annual-report-2024-25-excerpt.pdf")
IMF_PDF = os.path.join(ROOT, "starter-datasets", "india-macroeconomy", "03-imf-india-2025-article-iv-excerpt.pdf")

# extract_document() defaults to logging rejections to the real
# data/rejected_facts.jsonl (a graded deliverable, CLAUDE.md section 3.1).
# Redirect it here so running this file doesn't silently inflate that file.
_TEST_REJECTED_PATH = os.path.join(tempfile.mkdtemp(prefix="fact_layer_test_"), "rejected_facts.jsonl")


def _no_network(*args, **kwargs):
    raise AssertionError(
        "no network call should be attempted — this test must be fully "
        "servable from the committed cache/llm/ replay cache"
    )


@pytest.fixture(autouse=True)
def _replay_mode(monkeypatch):
    # Must match the provider/model that actually populated cache/llm/ during
    # the M3 live run: cache keys are sha256(provider+model+messages), so a
    # mismatched model string here would miss every lookup below. Deliberately
    # NOT redirecting llm._CACHE_DIR — this test reads the real committed
    # cache, not an isolated one.
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "post", _no_network)
    yield


_real_facts_cache: dict = {}


def _real_facts(pdf_path: str) -> list:
    """Facts for one document, reconstructed via the real pipeline in replay
    mode. Cached at module scope — re-parsing/re-extracting is not free."""
    if pdf_path not in _real_facts_cache:
        doc = parse_pdf(pdf_path)
        assert doc.error is None, f"failed to parse {pdf_path}: {doc.error}"
        filename = os.path.basename(pdf_path)
        defaults = extract_document_defaults(doc)
        budget = _DEMO_BUDGETS.get(filename) or default_budget(doc.n_pages)
        selected = select_pages(doc, budget)
        facts, _stats = extract_document(doc, selected, defaults, rejected_path=_TEST_REJECTED_PATH)
        _real_facts_cache[pdf_path] = facts
    return _real_facts_cache[pdf_path]


def _find(facts: list, **criteria) -> Fact:
    matched = [f for f in facts if all(getattr(f, k, None) == v for k, v in criteria.items())]
    assert matched, f"no fact matched {criteria!r} among {len(facts)} real extracted facts"
    return matched[0]


def _make_fact(subject_raw, measure_raw, value_raw, period_label, issuer, modality, doc_id="synthetic"):
    period = parse_period(period_label) if period_label else None
    return Fact(
        subject=subject_raw.lower(), measure=measure_raw.lower().replace(" ", "_"),
        value_kind=ValueKind.QUANTITY, value=parse_quantity(value_raw),
        qualifiers=Qualifiers(period=period, issuer=issuer),
        modality=modality,
        evidence=Evidence(doc_id=doc_id, page=1, char_start=0, char_end=len(value_raw),
                          verbatim_quote=value_raw, verified=True),
        subject_raw=subject_raw, measure_raw=measure_raw,
    )


# --------------------------------------------------------------------------
# (a) REAL IMF vs REAL RBI GDP projections, different issuers, both
# PROJECTED -> the GATE must short-circuit to INCOMPARABLE_ISSUER before
# ever comparing values. This is checked at the gate level, not through to
# adjudicate()'s final relation: the real RBI extraction's value_raw for
# this fact came through as bare "6.5" (unit parses as "count", not
# "percent" — the LLM didn't fold the "% change" column header into the
# cell value). adjudicate.py's _same_value()/_values_agree() compare
# magnitude only, never unit (a pre-existing property of the protected
# file, not something this fix touches), so 6.5-vs-6.6 reads as "close
# enough" regardless of unit and would misleadingly assert CORROBORATES
# here. The gate-level verdict below is unaffected by that and is exactly
# what this fix is responsible for; the full clean end-to-end relation is
# verified in the next test using CLAUDE.md's own worked-example numbers,
# which do carry a consistent unit.
# --------------------------------------------------------------------------

def test_real_imf_vs_rbi_gdp_projection_gate_is_incomparable_issuer():
    rbi_facts = _real_facts(RBI_PDF)
    imf_facts = _real_facts(IMF_PDF)

    rbi_gdp = _find(rbi_facts, subject_raw="Real GDP at Market Prices",
                    measure_raw="% change", modality=Modality.PROJECTED)
    imf_gdp = _find(imf_facts, subject_raw="India", measure_raw="real GDP growth",
                    modality=Modality.PROJECTED)

    assert rbi_gdp.qualifiers.issuer == "Reserve Bank of India"
    assert imf_gdp.qualifiers.issuer      # "IMF staff" in this extraction
    assert rbi_gdp.qualifiers.issuer != imf_gdp.qualifiers.issuer
    assert rbi_gdp.modality == Modality.PROJECTED and imf_gdp.modality == Modality.PROJECTED

    g = gate(rbi_gdp, imf_gdp)
    assert g.verdict == Verdict.INCOMPARABLE_ISSUER
    assert g.reason_code == "forecast_disagreement"
    assert g.cross_issuer is True
    assert rbi_gdp.qualifiers.issuer in g.explanation
    assert imf_gdp.qualifiers.issuer in g.explanation


def test_claude_md_worked_example_imf_6_5_vs_rbi_7_2_is_apparent_conflict():
    """CLAUDE.md section 2's own worked example: 'GDP growth 6.5% (IMF) vs
    7.2% (RBI) -> forecast disagreement'. Confirms the new code produces
    exactly that verdict for exactly that pair, end to end."""
    imf = _make_fact("India", "GDP growth", "6.5 percent", "FY2024/25",
                     "IMF", Modality.PROJECTED)
    rbi = _make_fact("India", "GDP growth", "7.2 percent", "FY2024/25",
                     "RBI", Modality.PROJECTED)

    g = gate(imf, rbi)
    assert g.verdict == Verdict.INCOMPARABLE_ISSUER
    assert g.reason_code == "forecast_disagreement"

    rel = adjudicate(imf, rbi, g)
    assert rel.relation == RelationType.APPARENT_CONFLICT
    assert rel.reason_code == "forecast_disagreement"
    assert "IMF" in rel.explanation and "RBI" in rel.explanation


# --------------------------------------------------------------------------
# (b) Same issuer, different vintage (point-in-time claims) -> the existing
# TEMPORAL_SUCCESSION / SUPERSEDES path is unaffected. GDP projections are
# duration periods and don't naturally produce this shape (that requires
# INSTANT periods, e.g. "as on DATE"), so this mirrors test_core.py's
# existing director-supersession case with an issuer now attached.
# --------------------------------------------------------------------------

def test_same_issuer_temporal_succession_path_unaffected():
    earlier = _make_fact("India", "Policy Rate", "6.5 percent", "as on 1 April 2024",
                         "Reserve Bank of India", Modality.ASSERTED, doc_id="d1")
    later = _make_fact("India", "Policy Rate", "6.0 percent", "as on 1 April 2025",
                       "Reserve Bank of India", Modality.ASSERTED, doc_id="d2")

    g = gate(earlier, later)
    assert g.verdict == Verdict.TEMPORAL_SUCCESSION
    assert g.cross_issuer is False   # same issuer -> untouched by this change

    rel = adjudicate(earlier, later, g)
    assert rel.relation == RelationType.SUPERSEDES


# --------------------------------------------------------------------------
# (c) Two ASSERTED historical facts, different issuers, genuinely different
# values -> CONTRADICTS, with cross_issuer noted in the explanation. Anchored
# on the real IMF ASSERTED fact; the real corpus didn't happen to extract a
# matching ASSERTED RBI figure for the same period, so the RBI side is a
# realistic hand-built counterpart (documented, not passed off as real).
# --------------------------------------------------------------------------

def test_asserted_cross_issuer_disagreement_is_contradicts_with_note():
    imf_facts = _real_facts(IMF_PDF)
    imf_gdp = _find(imf_facts, subject_raw="India", measure_raw="real GDP growth",
                    modality=Modality.ASSERTED)
    assert imf_gdp.qualifiers.issuer == "International Monetary Fund"
    assert imf_gdp.qualifiers.period is not None and imf_gdp.qualifiers.period.label == "FY2024/25"

    rbi_counterpart = _make_fact("India", "real GDP growth", "7.0 percent", "FY2024/25",
                                 "Reserve Bank of India", Modality.ASSERTED, doc_id="synthetic-rbi")

    g = gate(imf_gdp, rbi_counterpart)
    # Both ASSERTED -> must NOT short-circuit on issuer; proceeds to normal
    # value comparison, which is where a genuine contradiction is detected.
    assert g.verdict == Verdict.COMPARABLE
    assert g.cross_issuer is True

    rel = adjudicate(imf_gdp, rbi_counterpart, g)
    assert rel.relation == RelationType.CONTRADICTS
    assert "International Monetary Fund" in rel.explanation
    assert "Reserve Bank of India" in rel.explanation


# --------------------------------------------------------------------------
# (d) Same issuer on both facts -> completely unaffected (regression guard).
# Two distinct REAL IMF facts, same issuer, same value, same period.
# --------------------------------------------------------------------------

def test_same_issuer_pair_unaffected_by_issuer_check():
    imf_facts = _real_facts(IMF_PDF)
    fact1 = _find(imf_facts, subject_raw="economic growth", measure_raw="growth of",
                  modality=Modality.ASSERTED)
    fact2 = _find(imf_facts, subject_raw="India", measure_raw="real GDP growth",
                  modality=Modality.ASSERTED)

    assert fact1.qualifiers.issuer == fact2.qualifiers.issuer == "International Monetary Fund"

    g = gate(fact1, fact2)
    assert g.cross_issuer is False
    assert g.verdict != Verdict.INCOMPARABLE_ISSUER
    assert g.verdict == Verdict.COMPARABLE   # same value, same period -> as before this change

    rel = adjudicate(fact1, fact2, g)
    assert rel.relation == RelationType.CORROBORATES


# --------------------------------------------------------------------------
# Direct unit checks on the additive model/gate changes themselves.
# --------------------------------------------------------------------------

def test_qualifiers_diff_includes_issuer():
    a = Qualifiers(issuer="IMF")
    b = Qualifiers(issuer="RBI")
    assert a.diff(b)["issuer"] == ("IMF", "RBI")


def test_single_issuer_present_does_not_trigger_issuer_verdict():
    """Only one side having an issuer must not be treated as a mismatch —
    absence of a qualifier is not a conflict signal (CLAUDE.md invariant 6)."""
    with_issuer = _make_fact("India", "GDP growth", "6.5 percent", "FY2024/25",
                             "International Monetary Fund", Modality.PROJECTED)
    without_issuer = _make_fact("India", "GDP growth", "6.5 percent", "FY2024/25",
                                None, Modality.PROJECTED)

    g = gate(with_issuer, without_issuer)
    assert g.cross_issuer is False
    assert g.verdict != Verdict.INCOMPARABLE_ISSUER
