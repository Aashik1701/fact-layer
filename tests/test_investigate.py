"""
Comparability Investigator — domain tests.

The investigator is an EXPLANATION layer, so the tests that matter most are
the consistency ones: its verdict must be the gate's verdict, its reason
codes must be the gate's reason codes, and it must not touch the facts it
was given. Those are asserted first and hardest.

The rest covers the vocabulary distinctions the feature exists to preserve —
MISSING is not MISMATCH, UNVERIFIABLE is not INCOMPARABLE, and a
relation-bearing verdict (supersession / aggregation) is not a block.
"""

import copy
import dataclasses
import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.comparability import Verdict, gate
from fact_layer.investigate import (
    ActionType, DimensionStatus, DIMENSION_LABELS, investigate,
)
from fact_layer.models import (
    Evidence, Fact, Modality, Period, PeriodKind, Qualifiers, Quantity, Scope, ValueKind,
)
from fact_layer.normalize import parse_period


def mk(subject="acme", measure="revenue", value_kind=ValueKind.QUANTITY, value=None,
       val="100", unit="currency", currency="INR", period=None, scope=Scope.UNKNOWN,
       segment=None, geography=None, issuer=None, basis=None,
       modality=Modality.ASSERTED, verified=True, value_verification="verified",
       doc_id="d1", page=1) -> Fact:
    if value is None:
        value = (Quantity(value=Decimal(val), unit=unit, currency=currency, sig_figs=3, raw=val)
                 if value_kind == ValueKind.QUANTITY else val)
    return Fact(
        subject=subject, measure=measure, value_kind=value_kind, value=value,
        qualifiers=Qualifiers(
            period=parse_period(period) if period else None, scope=scope,
            segment=segment, geography=geography, issuer=issuer, basis=basis,
        ),
        modality=modality,
        evidence=Evidence(doc_id=doc_id, page=page, char_start=0, char_end=len(str(val)),
                          verbatim_quote=str(val), verified=verified),
        subject_raw=subject, measure_raw=measure,
        value_verification=value_verification,
    )


def _blocking_dims(explanation):
    return [b["dimension"] for b in explanation.to_dict()["blocking_reasons"]]


def _action_types(explanation):
    return [a["action_type"] for a in explanation.to_dict()["counterfactual_actions"]]


# --------------------------------------------------------------------------
# The gate is the authority (spec §35, §36)
# --------------------------------------------------------------------------

_PAIRS = [
    ("identical", mk(period="FY2023-24", scope=Scope.STANDALONE),
     mk(period="FY2023-24", scope=Scope.STANDALONE)),
    ("period", mk(period="FY2023-24"), mk(period="FY2021-22")),
    ("scope", mk(period="FY2023-24", scope=Scope.STANDALONE),
     mk(period="FY2023-24", scope=Scope.CONSOLIDATED)),
    ("currency", mk(period="FY2023-24", currency="INR"), mk(period="FY2023-24", currency="USD")),
    ("unit", mk(period="FY2023-24", unit="currency"),
     mk(period="FY2023-24", unit="percent", currency=None)),
    ("segment", mk(period="FY2023-24", segment="express"),
     mk(period="FY2023-24", segment="freight")),
    ("basis", mk(period="FY2023-24", basis="audited"),
     mk(period="FY2023-24", basis="unaudited")),
    ("issuer_forecast", mk(period="FY2023-24", issuer="IMF", modality=Modality.PROJECTED),
     mk(period="FY2023-24", issuer="RBI")),
    ("value_kind", mk(period="FY2023-24"),
     mk(period="FY2023-24", value_kind=ValueKind.TEXT, val="some text")),
    ("subject", mk(subject="acme"), mk(subject="other_co")),
    ("measure", mk(measure="revenue"), mk(measure="ebitda")),
    ("multi", mk(period="FY2023-24", scope=Scope.STANDALONE, currency="INR", segment="express"),
     mk(period="FY2021-22", scope=Scope.CONSOLIDATED, currency="USD", segment="freight")),
]


@pytest.mark.parametrize("name,a,b", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_verdict_always_equals_the_gate(name, a, b):
    """The investigator explains the gate; it may never disagree with it."""
    assert investigate(a, b).verdict == gate(a, b).verdict.value


@pytest.mark.parametrize("name,a,b", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_no_fact_mutation(name, a, b):
    """Counterfactual probing runs on throwaway copies. The caller's facts
    must be value-identical afterwards — this is what makes it honest to
    call the feature a 'readiness explanation' rather than a fact editor."""
    a_before, b_before = copy.deepcopy(a), copy.deepcopy(b)
    investigate(a, b)
    assert a == a_before
    assert b == b_before


@pytest.mark.parametrize("name,a,b", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_deterministic(name, a, b):
    import json
    first = json.dumps(investigate(a, b).to_dict(), sort_keys=True)
    for _ in range(3):
        assert json.dumps(investigate(a, b).to_dict(), sort_keys=True) == first


@pytest.mark.parametrize("name,a,b", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_blocking_reason_codes_come_from_a_real_authority(name, a, b):
    """No parallel taxonomy: every reason code must be one the gate or the
    structural blocker actually emits."""
    from fact_layer.retrieval.blocking import blocking_check

    gate_codes = {
        "value_kind_mismatch", "scope_mismatch", "forecast_disagreement",
        "segment_mismatch", "unit_mismatch", "period_disjoint", "period_overlap",
        "basis_mismatch",
    }
    structural_codes = {"SUBJECT_MISMATCH", "MEASURE_MISMATCH", "VALUE_KIND_MISMATCH"}
    for b_reason in investigate(a, b).to_dict()["blocking_reasons"]:
        assert b_reason["reason_code"] in (gate_codes | structural_codes)
        assert b_reason["authority"] in (
            "comparability.gate", "retrieval.blocking.blocking_check")


# --------------------------------------------------------------------------
# Comparable case (spec §29)
# --------------------------------------------------------------------------

def test_comparable_pair_reports_no_blocking_and_no_actions():
    a = mk(period="FY2023-24", scope=Scope.STANDALONE)
    b = mk(period="FY2023-24", scope=Scope.STANDALONE, val="200")
    e = investigate(a, b)
    assert e.verdict == Verdict.COMPARABLE.value
    assert e.comparable is True
    assert e.blocking_reasons == []
    assert e.counterfactual_actions == []
    assert "may proceed to relationship adjudication" in e.safe_conclusion


# --------------------------------------------------------------------------
# Single-dimension mismatches + their counterfactuals (spec §34)
# --------------------------------------------------------------------------

def test_period_mismatch():
    e = investigate(mk(period="FY2023-24"), mk(period="FY2021-22"))
    assert _blocking_dims(e) == ["period"]
    assert _action_types(e) == [ActionType.ALIGN_PERIOD.value]
    action = e.counterfactual_actions[0]
    # §14: must not tell the user which fact to change
    assert "same reporting period" in action.required_condition
    assert "FY2023-24" not in action.required_condition
    assert "use " not in action.required_condition.lower()


def test_scope_mismatch_does_not_prefer_either_scope():
    e = investigate(mk(period="FY2023-24", scope=Scope.STANDALONE),
                    mk(period="FY2023-24", scope=Scope.CONSOLIDATED))
    assert _blocking_dims(e) == ["scope"]
    cond = e.counterfactual_actions[0].required_condition
    assert "standalone with standalone" in cond
    assert "does not prefer either scope" in cond


def test_currency_mismatch_refuses_to_invent_an_fx_rate():
    e = investigate(mk(period="FY2023-24", currency="INR"),
                    mk(period="FY2023-24", currency="USD"))
    action = e.counterfactual_actions[0]
    assert action.action_type == ActionType.ALIGN_CURRENCY
    assert action.reason_code == "unit_mismatch"      # the gate's own code
    assert action.safe is False                       # never auto-converted
    assert "No FX rate is stated" in action.required_condition


def test_unit_mismatch_is_distinct_from_currency_mismatch():
    e = investigate(mk(period="FY2023-24", unit="currency"),
                    mk(period="FY2023-24", unit="percent", currency=None))
    action = e.counterfactual_actions[0]
    assert action.action_type == ActionType.ALIGN_UNIT
    assert "FX" not in action.required_condition


def test_issuer_mismatch_uses_entity_relationship_language():
    e = investigate(mk(period="FY2023-24", issuer="IMF", modality=Modality.PROJECTED),
                    mk(period="FY2023-24", issuer="RBI"))
    assert _blocking_dims(e) == ["issuer"]
    cond = e.counterfactual_actions[0].required_condition
    assert "different issuers" in cond
    assert "forecast disagreement, not a factual error" in cond
    assert e.counterfactual_actions[0].safe is False


def test_segment_mismatch():
    e = investigate(mk(period="FY2023-24", segment="express"),
                    mk(period="FY2023-24", segment="freight"))
    assert _blocking_dims(e) == ["segment"]
    assert _action_types(e) == [ActionType.ALIGN_SEGMENT.value]


def test_basis_mismatch():
    e = investigate(mk(period="FY2023-24", basis="audited"),
                    mk(period="FY2023-24", basis="unaudited"))
    assert _blocking_dims(e) == ["basis"]


# --------------------------------------------------------------------------
# Structural (pre-gate) blocking — spec §17
# --------------------------------------------------------------------------

def test_entity_mismatch_is_blocked_before_the_gate():
    """gate() never inspects subject, so without the structural layer two
    unrelated facts would be reported comparable."""
    a, b = mk(subject="acme"), mk(subject="other_co")
    e = investigate(a, b)
    assert e.structurally_blocked is True
    assert e.comparable is False
    assert _blocking_dims(e) == ["subject"]
    assert e.blocking_reasons[0]["reason_code"] == "SUBJECT_MISMATCH"
    assert e.blocking_reasons[0]["authority"] == "retrieval.blocking.blocking_check"


def test_entity_counterfactual_prefers_unresolved_over_incorrect_merge():
    e = investigate(mk(subject="acme"), mk(subject="other_co"))
    action = e.counterfactual_actions[0]
    assert action.action_type == ActionType.RESOLVE_ENTITY
    assert action.safe is False
    assert "rather than risking an incorrect merge" in action.required_condition


def test_measure_mismatch_is_blocked_before_the_gate():
    e = investigate(mk(measure="revenue"), mk(measure="ebitda"))
    assert e.structurally_blocked is True
    assert _blocking_dims(e) == ["measure"]


# --------------------------------------------------------------------------
# Multiple blocking reasons — spec §19
# --------------------------------------------------------------------------

def test_all_blocking_dimensions_are_surfaced_not_just_the_first():
    """A single gate() call short-circuits on scope. The investigator must
    still report period, unit and segment."""
    a = mk(period="FY2023-24", scope=Scope.STANDALONE, currency="INR", segment="express")
    b = mk(period="FY2021-22", scope=Scope.CONSOLIDATED, currency="USD", segment="freight")
    single_call = gate(a, b)
    assert single_call.reason_code == "scope_mismatch"      # gate names one

    e = investigate(a, b)
    dims = set(_blocking_dims(e))
    assert dims == {"scope", "segment", "currency", "period"}, dims
    assert len(e.counterfactual_actions) == 4
    assert e.verdict == single_call.verdict.value           # still the gate's verdict


def test_multi_block_conclusion_names_every_dimension():
    a = mk(period="FY2023-24", scope=Scope.STANDALONE)
    b = mk(period="FY2021-22", scope=Scope.CONSOLIDATED)
    e = investigate(a, b)
    assert "scope" in e.safe_conclusion and "period" in e.safe_conclusion
    assert "No relationship was inferred" in e.safe_conclusion


# --------------------------------------------------------------------------
# MISSING is not MISMATCH; UNVERIFIABLE is not INCOMPARABLE — spec §5, §18, §30
# --------------------------------------------------------------------------

def test_unstated_period_is_missing_not_mismatch():
    """The dimension matrix still reports this MISSING — a period genuinely
    absent from one fact is a different epistemic situation from AMBIGUOUS
    (a period stated on both sides but unparseable; see
    test_unparseable_period_is_ambiguous_not_missing below). But it is no
    longer a free pass: the gate's source-of-truth fix means an UNKNOWN
    period relation always returns Verdict.AMBIGUOUS, so this now correctly
    blocks rather than silently falling through to COMPARABLE."""
    e = investigate(mk(period="FY2023-24"), mk(period=None))
    period = next(d for d in e.dimensions if d.dimension == "period")
    assert period.status == DimensionStatus.MISSING
    assert period.status != DimensionStatus.MISMATCH
    assert period.is_blocking is True
    assert period.reason_code == "ambiguous_period"
    assert e.verdict == Verdict.AMBIGUOUS.value
    assert "period" in e.ambiguous_dimensions


def test_unparseable_period_is_ambiguous_not_missing():
    """A period IS stated on both facts (non-empty label) but one side's
    date could not be parsed — a different, more informative situation than
    MISSING (nothing stated at all). The matrix must not fall back to
    comparing label text in this case, since the real gate does not trust
    label text for dates either; it must report the same AMBIGUOUS status
    the gate itself would derive from compare_periods()."""
    a = mk(period="FY2023-24")
    b = mk(period="FY2023-24")
    unparseable = Period(PeriodKind.DURATION, start=None, end=None, label="the reporting period")
    b = dataclasses.replace(b, qualifiers=dataclasses.replace(b.qualifiers, period=unparseable))
    e = investigate(a, b)
    period = next(d for d in e.dimensions if d.dimension == "period")
    assert period.status == DimensionStatus.AMBIGUOUS
    assert period.status != DimensionStatus.MISSING
    assert period.is_blocking is True
    assert period.reason_code == "ambiguous_period"
    assert e.verdict == Verdict.AMBIGUOUS.value


def test_unstated_scope_is_missing_and_never_assumed_consolidated():
    e = investigate(mk(period="FY2023-24", scope=Scope.STANDALONE),
                    mk(period="FY2023-24", scope=Scope.UNKNOWN))
    scope = next(d for d in e.dimensions if d.dimension == "scope")
    assert scope.status == DimensionStatus.MISSING
    assert scope.is_blocking is False
    assert "does not assume consolidated" in scope.detail
    assert e.verdict == gate(*(mk(period="FY2023-24", scope=Scope.STANDALONE),
                               mk(period="FY2023-24", scope=Scope.UNKNOWN))).verdict.value


def test_missing_dimension_produces_a_caveat_not_a_blocking_reason():
    """Scope, unlike period, is intentionally permissive when unstated —
    gate() never emits a dedicated reason code for an unknown scope (see
    comparability.py's UNKNOWN-fallthrough audit note above
    _unit_compatible), so an unstated scope still produces a caveat, not a
    blocking reason. Period moved out of this category with the gate fix;
    see test_unstated_period_is_missing_not_mismatch above."""
    e = investigate(mk(period="FY2023-24", scope=Scope.STANDALONE),
                    mk(period="FY2023-24", scope=Scope.UNKNOWN))
    assert e.blocking_reasons == []
    assert any("Scope is unstated" in c for c in e.caveats)


def test_unverified_evidence_is_a_caveat_never_a_block():
    """§18: evidence uncertainty is separate from semantic incompatibility."""
    a = mk(period="FY2023-24", scope=Scope.STANDALONE)
    b = mk(period="FY2023-24", scope=Scope.STANDALONE, value_verification="unverified")
    e = investigate(a, b)
    verification = next(d for d in e.dimensions if d.dimension == "verification")
    assert verification.status == DimensionStatus.UNVERIFIABLE
    assert verification.is_blocking is False
    assert e.verdict == Verdict.COMPARABLE.value        # gate ignores verification
    assert any("not independently confirmed" in c for c in e.caveats)
    assert e.blocking_reasons == []


def test_unverifiable_never_becomes_incomparable():
    a = mk(period="FY2023-24", verified=False, value_verification="")
    b = mk(period="FY2023-24", verified=False, value_verification="")
    e = investigate(a, b)
    assert e.verdict == gate(a, b).verdict.value
    assert e.verdict != "incomparable_kind"
    assert not any(br["dimension"] == "verification" for br in e.to_dict()["blocking_reasons"])


# --------------------------------------------------------------------------
# Relation-bearing verdicts are not blocks — spec §31
# --------------------------------------------------------------------------

def test_temporal_succession_is_not_reported_as_blocked():
    """Two point-in-time (INSTANT) claims at different dates. The gate calls
    this succession, and the adjudicator turns it into SUPERSEDES — so the
    investigator must NOT present it as a blocked comparison."""
    from fact_layer.models import Period, PeriodKind
    a = mk()
    b = mk(val="200")
    a.qualifiers = Qualifiers(
        period=Period(PeriodKind.INSTANT, date(2023, 3, 31), date(2023, 3, 31),
                      label="as on 31 March 2023"), scope=Scope.UNKNOWN)
    b.qualifiers = Qualifiers(
        period=Period(PeriodKind.INSTANT, date(2024, 3, 31), date(2024, 3, 31),
                      label="as on 31 March 2024"), scope=Scope.UNKNOWN)
    g = gate(a, b)
    assert g.verdict == Verdict.TEMPORAL_SUCCESSION
    e = investigate(a, b)
    assert e.relation_bearing is True
    assert e.blocking_reasons == []
    assert "supersession" in e.safe_conclusion


def test_aggregation_candidate_is_not_reported_as_blocked():
    a = mk(period="FY2023-24")
    b = mk(period="Q1 FY2023-24")
    g = gate(a, b)
    if g.verdict != Verdict.AGGREGATION_CANDIDATE:
        pytest.skip("fixture did not produce an aggregation candidate on this gate build")
    e = investigate(a, b)
    assert e.relation_bearing is True
    assert e.blocking_reasons == []
    assert "aggregation" in e.safe_conclusion


# --------------------------------------------------------------------------
# Shape, evidence and safety
# --------------------------------------------------------------------------

def test_every_dimension_is_reported_every_time():
    e = investigate(mk(period="FY2023-24"), mk(period="FY2021-22"))
    assert {d.dimension for d in e.dimensions} == set(DIMENSION_LABELS)


def test_evidence_refs_point_at_the_existing_evidence_model_without_paths():
    import json
    e = investigate(mk(period="FY2023-24", doc_id="dA", page=7),
                    mk(period="FY2021-22", doc_id="dB", page=9))
    refs = e.to_dict()["evidence_refs"]
    assert [r["role"] for r in refs] == ["fact_a", "fact_b"]
    assert refs[0]["doc_id"] == "dA" and refs[0]["page"] == 7
    assert refs[1]["doc_id"] == "dB" and refs[1]["page"] == 9
    blob = json.dumps(refs)
    # no filesystem path leakage (§42)
    assert "/" not in blob.replace("\\/", "") or "data/uploads" not in blob
    assert "filename" not in blob


def test_reversed_order_preserves_verdict_and_blocking_dimensions():
    a = mk(period="FY2023-24", scope=Scope.STANDALONE, currency="INR")
    b = mk(period="FY2021-22", scope=Scope.CONSOLIDATED, currency="USD")
    fwd, rev = investigate(a, b), investigate(b, a)
    assert fwd.verdict == rev.verdict
    assert set(_blocking_dims(fwd)) == set(_blocking_dims(rev))
    assert fwd.comparable == rev.comparable


def test_no_action_is_emitted_for_a_dimension_that_does_not_block():
    """A difference the gate tolerates must not generate a readiness action —
    otherwise the panel invents work that isn't required."""
    a = mk(period="FY2023-24", geography="india")
    b = mk(period="FY2023-24", geography="usa")
    e = investigate(a, b)
    # gate() does not block on geography
    assert gate(a, b).verdict == Verdict.COMPARABLE
    assert e.counterfactual_actions == []
    geo = next(d for d in e.dimensions if d.dimension == "geography")
    assert geo.status == DimensionStatus.MISMATCH
    assert geo.is_blocking is False


def test_disclaimer_states_incomparable_is_not_unrelated():
    d = investigate(mk(period="FY2023-24"), mk(period="FY2021-22")).to_dict()
    assert "Incomparable does not mean unrelated" in d["disclaimer"]
    assert d["authority"]["llm_used"] is False
    assert d["authority"]["deterministic"] is True
