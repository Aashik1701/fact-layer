"""
Temporal Knowledge (fact_layer/temporal.py) — domain tests.

The projection must never: interpolate a missing period, guess an order
for a period it cannot parse, merge scope/modality series, invent a
relation between two points, or substitute a source's publication date for
a fact's own period. Every test below is aimed at one of those specific
failure modes, not general coverage for its own sake.
"""

import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.models import (
    Evidence, Fact, Modality, Period, PeriodKind, Qualifiers, Quantity,
    Relation, RelationType, Scope, ValueKind,
)
from fact_layer.store import Store, _STORE_PATH
from fact_layer.temporal import entity_history, entity_measures


def _mkfact(subject="acme", measure="revenue", val="100", period_label="FY2023-24",
            period_start=None, period_end=None, period_kind=PeriodKind.DURATION,
            scope=Scope.UNKNOWN, modality=Modality.ASSERTED,
            doc_id="docA", page=1, fact_id_suffix="") -> Fact:
    period = None
    if period_label is not None:
        start = period_start or date(2023, 4, 1)
        end = period_end or date(2024, 3, 31)
        period = Period(period_kind, start, end, label=period_label)
    return Fact(
        subject=subject, measure=measure, value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal(val), unit="currency", currency="INR",
                       sig_figs=4, raw=val),
        qualifiers=Qualifiers(period=period, scope=scope),
        modality=modality,
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(val),
                          verbatim_quote=f"{measure} {val}{fact_id_suffix}", verified=True),
        subject_raw=subject, measure_raw=measure, value_verification="verified",
    )


@pytest.fixture(scope="module")
def real_store():
    return Store.load(_STORE_PATH)


# --------------------------------------------------------------------------
# Basic history: periods, ordering, values, sources
# --------------------------------------------------------------------------

def test_multi_year_history_ordered_chronologically():
    store = Store()
    f2022 = _mkfact(val="80", period_label="FY2021-22", period_start=date(2021, 4, 1), period_end=date(2022, 3, 31))
    f2024 = _mkfact(val="120", period_label="FY2023-24", period_start=date(2023, 4, 1), period_end=date(2024, 3, 31))
    f2023 = _mkfact(val="95", period_label="FY2022-23", period_start=date(2022, 4, 1), period_end=date(2023, 3, 31))
    for f in (f2024, f2022, f2023):    # inserted out of order deliberately
        store.facts[f.fact_id] = f

    history = entity_history(store, "acme", "revenue")
    assert history.total_facts == 3
    assert len(history.series) == 1
    labels = [p.period_label for p in history.series[0].points]
    assert labels == ["FY2021-22", "FY2022-23", "FY2023-24"]
    values = [p.value["raw"] for p in history.series[0].points]
    assert values == ["80", "95", "120"]


def test_source_context_present_and_correct():
    store = Store()
    f = _mkfact(doc_id="annual_report_2024", page=47)
    store.facts[f.fact_id] = f
    store.ingested_docs = {"annual_report_2024": "Annual Report 2024.pdf"}
    history = entity_history(store, "acme", "revenue")
    point = history.series[0].points[0]
    assert point.doc_id == "annual_report_2024"
    assert point.doc_filename == "Annual Report 2024.pdf"
    assert point.page == 47


# --------------------------------------------------------------------------
# Period semantics — kind classification is presentation only
# --------------------------------------------------------------------------

def test_quarter_and_fy_periods_remain_distinct():
    store = Store()
    fy = _mkfact(val="120", period_label="FY2023-24")
    q1 = _mkfact(val="32", period_label="Q1 FY2024-25",
                 period_start=date(2024, 4, 1), period_end=date(2024, 6, 30),
                 fact_id_suffix="_q1")
    store.facts[fy.fact_id] = fy
    store.facts[q1.fact_id] = q1
    history = entity_history(store, "acme", "revenue")
    kinds = {p.period_label: p.period_kind for p in history.series[0].points}
    assert kinds["FY2023-24"] == "fiscal_year"
    assert kinds["Q1 FY2024-25"] == "quarter"


def test_as_of_instant_period_kind():
    store = Store()
    f = _mkfact(val="500", period_label="as on 31 March 2025",
                period_start=date(2025, 3, 31), period_end=date(2025, 3, 31),
                period_kind=PeriodKind.INSTANT)
    store.facts[f.fact_id] = f
    history = entity_history(store, "acme", "revenue")
    assert history.series[0].points[0].period_kind == "as_of"


def test_ambiguous_period_never_ordered_or_guessed():
    """A fact whose period phrase parse_period() could not resolve to a
    start date must never be silently placed on the chronological axis."""
    store = Store()
    good = _mkfact(val="100", period_label="FY2023-24")
    bad = Fact(
        subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY,
        value=Quantity(value=Decimal("999"), unit="currency", currency="INR", sig_figs=3, raw="999"),
        qualifiers=Qualifiers(period=Period(PeriodKind.DURATION, None, None, label="sometime recently")),
        evidence=Evidence(doc_id="docA", page=2, char_start=0, char_end=3,
                          verbatim_quote="revenue 999", verified=True),
        subject_raw="acme", measure_raw="revenue",
    )
    store.facts[good.fact_id] = good
    store.facts[bad.fact_id] = bad

    history = entity_history(store, "acme", "revenue")
    assert len(history.series[0].points) == 1
    assert len(history.ambiguous_period_points) == 1
    assert history.ambiguous_period_points[0].fact_id == bad.fact_id
    assert history.ambiguous_period_points[0].period_kind == "unknown"


def test_unstated_period_is_not_ambiguous_it_is_unstated():
    store = Store()
    f = _mkfact(period_label=None)   # no period at all
    store.facts[f.fact_id] = f
    history = entity_history(store, "acme", "revenue")
    assert len(history.ambiguous_period_points) == 1
    assert history.ambiguous_period_points[0].period_kind == "unstated"


# --------------------------------------------------------------------------
# Scope / modality grouping — never silently merged
# --------------------------------------------------------------------------

def test_standalone_and_consolidated_are_separate_series():
    store = Store()
    standalone = _mkfact(val="110", scope=Scope.STANDALONE)
    consolidated = _mkfact(val="120", scope=Scope.CONSOLIDATED, fact_id_suffix="_c")
    store.facts[standalone.fact_id] = standalone
    store.facts[consolidated.fact_id] = consolidated

    history = entity_history(store, "acme", "revenue")
    assert len(history.series) == 2
    scopes = {s.scope for s in history.series}
    assert scopes == {"standalone", "consolidated"}


def test_reported_and_guidance_are_separate_series():
    store = Store()
    reported = _mkfact(val="137", modality=Modality.ASSERTED)
    guidance = _mkfact(val="145", modality=Modality.PROJECTED, fact_id_suffix="_g")
    store.facts[reported.fact_id] = reported
    store.facts[guidance.fact_id] = guidance

    history = entity_history(store, "acme", "revenue")
    assert len(history.series) == 2
    modalities = {s.modality for s in history.series}
    assert modalities == {"asserted", "projected"}


# --------------------------------------------------------------------------
# Duplicate periods: never pick one, never merge
# --------------------------------------------------------------------------

def test_duplicate_period_same_scope_keeps_both_points():
    store = Store()
    a = _mkfact(val="120", doc_id="docA")
    b = _mkfact(val="121", doc_id="docB", fact_id_suffix="_b")
    store.facts[a.fact_id] = a
    store.facts[b.fact_id] = b
    history = entity_history(store, "acme", "revenue")
    assert len(history.series) == 1
    assert len(history.series[0].points) == 2


# --------------------------------------------------------------------------
# Supersession / relation surfacing — only ever copied from Store.relations
# --------------------------------------------------------------------------

def test_supersession_relation_surfaced_on_both_points():
    store = Store()
    earlier = _mkfact(val="100", doc_id="docA", page=1)
    later = _mkfact(val="105", doc_id="docB", page=1, fact_id_suffix="_later")
    store.facts[earlier.fact_id] = earlier
    store.facts[later.fact_id] = later
    store.relations = [
        Relation(later.fact_id, earlier.fact_id, RelationType.SUPERSEDES, 0.9,
                 "temporal_succession", "The later statement replaces the earlier one.", {}),
    ]
    history = entity_history(store, "acme", "revenue")
    all_points = {p.fact_id: p for s in history.series for p in s.points}
    assert all_points[earlier.fact_id].related_points[0].relation == "supersedes"
    assert all_points[later.fact_id].related_points[0].relation == "supersedes"


def test_newer_fact_alone_is_not_marked_superseded():
    """Being chronologically later is not supersession — only an actual
    Store.relations SUPERSEDES edge is."""
    store = Store()
    older = _mkfact(val="80", period_label="FY2021-22", period_start=date(2021, 4, 1), period_end=date(2022, 3, 31))
    newer = _mkfact(val="120", period_label="FY2023-24")
    store.facts[older.fact_id] = older
    store.facts[newer.fact_id] = newer
    # No relations recorded at all.
    history = entity_history(store, "acme", "revenue")
    for point in history.series[0].points:
        assert point.related_points == []


def test_relation_outside_this_entity_measure_never_leaks_in():
    store = Store()
    a = _mkfact(val="100")
    other = _mkfact(subject="other_co", measure="ebitda", val="55", fact_id_suffix="_other")
    store.facts[a.fact_id] = a
    store.facts[other.fact_id] = other
    store.relations = [
        Relation(a.fact_id, other.fact_id, RelationType.CORROBORATES, 0.5, "x", "x", {}),
    ]
    history = entity_history(store, "acme", "revenue")
    assert history.series[0].points[0].related_points == []


# --------------------------------------------------------------------------
# Source publication date vs. fact period — never conflated
# --------------------------------------------------------------------------

def test_fact_period_independent_of_source_document_id():
    """The fact's period comes only from qualifiers.period; the document a
    fact was extracted from is a separate field and must never be used to
    place the fact on the temporal axis."""
    store = Store()
    f = _mkfact(val="120", period_label="FY2023-24",
                doc_id="annual_report_published_2025")
    store.facts[f.fact_id] = f
    history = entity_history(store, "acme", "revenue")
    point = history.series[0].points[0]
    assert point.period_label == "FY2023-24"
    assert point.period_start == "2023-04-01"
    assert point.doc_id == "annual_report_published_2025"


# --------------------------------------------------------------------------
# Discovery: entity_measures()
# --------------------------------------------------------------------------

def test_entity_measures_lists_only_this_subjects_measures():
    store = Store()
    a = _mkfact(subject="acme", measure="revenue")
    b = _mkfact(subject="acme", measure="ebitda", fact_id_suffix="_b")
    c = _mkfact(subject="other_co", measure="revenue", fact_id_suffix="_c")
    for f in (a, b, c):
        store.facts[f.fact_id] = f
    assert entity_measures(store, "acme") == ["ebitda", "revenue"]
    assert entity_measures(store, "unknown_subject") == []


# --------------------------------------------------------------------------
# Incremental ingestion: new document -> new period appears
# --------------------------------------------------------------------------

def test_incremental_ingest_extends_history(monkeypatch):
    import fact_layer.store as store_mod
    from fact_layer.extract import ExtractionStats
    from fact_layer.parse import Document

    store = store_mod.Store()
    existing = _mkfact(val="120", period_label="FY2023-24", doc_id="docA", page=3)
    store.facts[existing.fact_id] = existing
    store.clusters[existing.cluster_key()] = [existing.fact_id]
    store.ingested_docs["docA"] = "old.pdf"

    history_before = entity_history(store, "acme", "revenue")
    assert len(history_before.series[0].points) == 1

    new_fact = _mkfact(val="137", period_label="FY2024-25",
                       period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
                       doc_id="docB", page=9, fact_id_suffix="_new")

    monkeypatch.setattr(store_mod, "parse_pdf",
                        lambda path: Document(doc_id="docB", filename="new.pdf", n_pages=1, pages=[]))
    monkeypatch.setattr(store_mod, "extract_document_defaults", lambda doc: {})
    monkeypatch.setattr(store_mod, "select_pages", lambda doc, budget: [])
    monkeypatch.setattr(
        store_mod, "extract_document",
        lambda doc, selected, defaults, rejected_path: ([new_fact], ExtractionStats(proposed=1, verified=1)),
    )
    monkeypatch.setattr(store_mod, "resolve_facts", lambda facts, resolver: (facts, resolver))

    store.ingest("new.pdf", relationship_mode="bruteforce")

    history_after = entity_history(store, "acme", "revenue")
    assert len(history_after.series[0].points) == 2
    labels = {p.period_label for p in history_after.series[0].points}
    assert labels == {"FY2023-24", "FY2024-25"}


# --------------------------------------------------------------------------
# Real corpus
# --------------------------------------------------------------------------

def test_real_corpus_multi_period_history(real_store):
    """§52 case 1: a real (subject, measure) with 3+ periods. Verified
    present in this corpus at plan time: ('total income', 'revenue')."""
    history = entity_history(real_store, "total income", "revenue")
    all_points = [p for s in history.series for p in s.points] + history.ambiguous_period_points
    assert len(all_points) >= 3
    assert history.total_facts == len(all_points)


def test_real_corpus_no_mutation():
    import copy
    store = Store.load(_STORE_PATH)
    facts_before = copy.deepcopy(store.facts)
    relations_before = copy.deepcopy(store.relations)
    entity_history(store, "total income", "revenue")
    entity_measures(store, "total income")
    assert store.facts == facts_before
    assert store.relations == relations_before
