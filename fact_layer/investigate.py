"""
Comparability Investigator + Counterfactual Comparison Readiness.

WHAT THIS IS
--------------------------------------------------------------------------
A deterministic EXPLANATION layer over `fact_layer.comparability.gate()`.
It answers, for one ordered pair of facts:

    * is this pair comparable, and by whose authority
    * which dimensions agree, which conflict, which are simply unstated
    * every dimension that blocks — not just the first one
    * what the system deliberately refused to infer
    * what would have to be true before a valid comparison could happen
    * which evidence each determination came from

WHAT THIS IS NOT
--------------------------------------------------------------------------
It is NOT a second comparability engine. `gate()` remains the sole
authority for the verdict: `ComparabilityExplanation.verdict` is copied
verbatim from `gate(a, b).verdict`, and every blocking reason code is one
`gate()` itself emitted. There is no LLM anywhere in this module — the
same two facts always produce byte-identical output.

It is also NOT a fact mutation engine. Counterfactual actions describe
what WOULD need to be true; nothing here rewrites a period, converts a
currency, or resolves an entity. The probing described below operates on
throwaway copies and the caller's facts are never touched.

TWO AUTHORITIES, IN ORDER
--------------------------------------------------------------------------
`gate()` deliberately does not compare subject or measure — it assumes its
callers only hand it facts from the same `cluster_key()` bucket. Handing it
`revenue` and `ebitda` would therefore return COMPARABLE, which is true
only in the narrow sense that nothing gate() inspects disagrees. So this
module applies the project's OTHER existing structural authority first,
`fact_layer.retrieval.blocking.blocking_check()`, whose SUBJECT_MISMATCH /
MEASURE_MISMATCH / VALUE_KIND_MISMATCH codes are reused verbatim rather
than reinvented. Structural block first, then the gate.

HOW ALL BLOCKING DIMENSIONS ARE FOUND WITHOUT DUPLICATING GATE LOGIC
--------------------------------------------------------------------------
`gate()` short-circuits: it returns on the first dimension that fails, so a
single call names one reason even when four dimensions disagree. Section 19
of the spec requires the complete set.

Rather than re-implement the gate's per-dimension predicates (which would
create exactly the parallel engine this module must not be), the blocking
set is discovered by PEELING: ask the real gate what blocks, hypothetically
align that one dimension on a throwaway copy, ask the real gate again, and
repeat until it stops blocking or stops making progress. Every reason code
in the result was therefore produced by `gate()` itself, on inputs that
differ from the caller's only in the dimensions already reported. The loop
is bounded by the number of alignable dimensions and has an explicit
no-progress guard, so it always terminates.

This is also precisely what a counterfactual IS — "align this and the gate
stops objecting" — so the readiness actions fall out of the same pass
instead of being asserted separately and drifting from it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .comparability import GateResult, Verdict, gate
from .models import Fact, Quantity, Scope, ValueKind
from .retrieval.blocking import blocking_check


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

class DimensionStatus(str, Enum):
    """Deliberately not a boolean. `MISSING` (the source never stated it) is
    a different epistemic situation from `MISMATCH` (the sources stated
    different things), and `UNVERIFIABLE` (we cannot trust what was stated)
    is different again. Collapsing them is how a system starts reporting
    confident conflicts it has not earned."""
    MATCH = "match"
    MISMATCH = "mismatch"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    UNVERIFIABLE = "unverifiable"
    NOT_APPLICABLE = "not_applicable"


class ActionType(str, Enum):
    ALIGN_PERIOD = "align_period"
    ALIGN_UNIT = "align_unit"
    ALIGN_CURRENCY = "align_currency"
    ALIGN_SCOPE = "align_scope"
    ALIGN_ISSUER = "align_issuer"
    ALIGN_GEOGRAPHY = "align_geography"
    ALIGN_SEGMENT = "align_segment"
    ALIGN_BASIS = "align_basis"
    RESOLVE_ENTITY = "resolve_entity"
    RESOLVE_MEASURE = "resolve_measure"
    RESOLVE_VALUE_KIND = "resolve_value_kind"
    VERIFY_EVIDENCE = "verify_evidence"
    RESOLVE_AMBIGUITY = "resolve_ambiguity"


# Dimension keys, in the order the UI renders them.
DIM_SUBJECT = "subject"
DIM_MEASURE = "measure"
DIM_VALUE_KIND = "value_kind"
DIM_PERIOD = "period"
DIM_UNIT = "unit"
DIM_CURRENCY = "currency"
DIM_SCOPE = "scope"
DIM_ISSUER = "issuer"
DIM_SEGMENT = "segment"
DIM_GEOGRAPHY = "geography"
DIM_BASIS = "basis"
DIM_VERIFICATION = "verification"

DIMENSION_ORDER = [
    DIM_SUBJECT, DIM_MEASURE, DIM_VALUE_KIND, DIM_PERIOD, DIM_UNIT,
    DIM_CURRENCY, DIM_SCOPE, DIM_ISSUER, DIM_SEGMENT, DIM_GEOGRAPHY,
    DIM_BASIS, DIM_VERIFICATION,
]

DIMENSION_LABELS = {
    DIM_SUBJECT: "Entity", DIM_MEASURE: "Measure", DIM_VALUE_KIND: "Value kind",
    DIM_PERIOD: "Period", DIM_UNIT: "Unit", DIM_CURRENCY: "Currency",
    DIM_SCOPE: "Scope", DIM_ISSUER: "Issuer", DIM_SEGMENT: "Segment",
    DIM_GEOGRAPHY: "Geography", DIM_BASIS: "Basis",
    DIM_VERIFICATION: "Verification",
}

# gate() reason_code -> the dimension it is about. Every key here is a
# literal reason_code emitted by comparability.gate(); adding a code to the
# gate without adding it here degrades gracefully (the peel stops and the
# reason is still reported), it does not produce a wrong answer.
_REASON_TO_DIMENSION = {
    "value_kind_mismatch": DIM_VALUE_KIND,
    "scope_mismatch": DIM_SCOPE,
    "forecast_disagreement": DIM_ISSUER,
    "segment_mismatch": DIM_SEGMENT,
    "unit_mismatch": DIM_UNIT,
    "period_disjoint": DIM_PERIOD,
    "period_overlap": DIM_PERIOD,
    "basis_mismatch": DIM_BASIS,
}

# blocking_check() reason -> dimension. Same contract, different authority.
_BLOCKING_TO_DIMENSION = {
    "SUBJECT_MISMATCH": DIM_SUBJECT,
    "MEASURE_MISMATCH": DIM_MEASURE,
    "VALUE_KIND_MISMATCH": DIM_VALUE_KIND,
}

# Verdicts that are NOT a block: the adjudicator turns these into real
# relations (SUPERSEDES / AGGREGATES_INTO). Reporting them as "blocked"
# would tell an evaluator the opposite of what the system did.
RELATION_BEARING_VERDICTS = frozenset({
    Verdict.TEMPORAL_SUCCESSION,
    Verdict.AGGREGATION_CANDIDATE,
})

_VERIFIED_STATES = frozenset({"verified", "verified_with_context"})


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------

@dataclass
class DimensionReport:
    dimension: str
    label: str
    status: DimensionStatus
    fact_a_value: Optional[str]
    fact_b_value: Optional[str]
    detail: str = ""
    # True only when this dimension is named by an authority (gate or
    # structural blocking) as a reason the pair cannot be compared.
    is_blocking: bool = False
    reason_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "label": self.label,
            "status": self.status.value,
            "fact_a": self.fact_a_value, "fact_b": self.fact_b_value,
            "detail": self.detail, "is_blocking": self.is_blocking,
            "reason_code": self.reason_code,
        }


@dataclass
class CounterfactualAction:
    dimension: str
    action_type: ActionType
    current_status: DimensionStatus
    fact_a_value: Optional[str]
    fact_b_value: Optional[str]
    required_condition: str
    # `safe=False` marks an action this system will not perform automatically
    # even in principle — an FX conversion with no stated rate, or an entity
    # merge below confidence. It is a warning against "just normalise it",
    # not a difficulty rating.
    safe: bool = True
    reason_code: Optional[str] = None
    authority: str = "comparability.gate"

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "action_type": self.action_type.value,
            "current_status": self.current_status.value,
            "fact_a": self.fact_a_value, "fact_b": self.fact_b_value,
            "required_condition": self.required_condition,
            "safe": self.safe, "reason_code": self.reason_code,
            "authority": self.authority,
        }


@dataclass
class ComparabilityExplanation:
    fact_a_id: str
    fact_b_id: str
    verdict: str                      # verbatim from gate(a, b).verdict
    comparable: bool
    relation_bearing: bool            # temporal succession / aggregation
    structurally_blocked: bool        # failed blocking_check() before the gate
    gate_reason_code: str
    gate_explanation: str
    dimensions: list[DimensionReport] = field(default_factory=list)
    blocking_reasons: list[dict] = field(default_factory=list)
    passing_dimensions: list[str] = field(default_factory=list)
    ambiguous_dimensions: list[str] = field(default_factory=list)
    counterfactual_actions: list[CounterfactualAction] = field(default_factory=list)
    safe_conclusion: str = ""
    caveats: list[str] = field(default_factory=list)
    evidence_refs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fact_a_id": self.fact_a_id,
            "fact_b_id": self.fact_b_id,
            "verdict": self.verdict,
            "comparable": self.comparable,
            "relation_bearing": self.relation_bearing,
            "structurally_blocked": self.structurally_blocked,
            "gate_reason_code": self.gate_reason_code,
            "gate_explanation": self.gate_explanation,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "blocking_reasons": list(self.blocking_reasons),
            "passing_dimensions": list(self.passing_dimensions),
            "ambiguous_dimensions": list(self.ambiguous_dimensions),
            "counterfactual_actions": [a.to_dict() for a in self.counterfactual_actions],
            "safe_conclusion": self.safe_conclusion,
            "caveats": list(self.caveats),
            "evidence_refs": list(self.evidence_refs),
            "authority": {
                "verdict_from": "fact_layer.comparability.gate",
                "structural_block_from": "fact_layer.retrieval.blocking.blocking_check",
                "deterministic": True,
                "llm_used": False,
            },
            "disclaimer": (
                "Incomparable does not mean unrelated: it means this system cannot "
                "validly compare the two facts under the current gate. Counterfactual "
                "actions describe what would need to be true — no fact was modified."
            ),
        }


# --------------------------------------------------------------------------
# Display helpers (read-only; never used to make a decision)
# --------------------------------------------------------------------------

def _period_label(f: Fact) -> Optional[str]:
    p = f.qualifiers.period
    return p.label if p is not None else None


def _unit_of(f: Fact) -> Optional[str]:
    return f.value.unit if isinstance(f.value, Quantity) else None


def _currency_of(f: Fact) -> Optional[str]:
    return f.value.currency if isinstance(f.value, Quantity) else None


def _scope_label(f: Fact) -> Optional[str]:
    s = f.qualifiers.scope
    if s is None or s == Scope.UNKNOWN:
        return None
    return s.value


def _verification_label(f: Fact) -> str:
    ev_ok = bool(f.evidence and f.evidence.verified)
    vv = f.value_verification or ""
    if not ev_ok:
        return "evidence unverified"
    if vv == "":
        return "span verified; value check not evaluated"
    return f"span verified; value {vv}"


def _is_verified(f: Fact) -> bool:
    if not (f.evidence and f.evidence.verified):
        return False
    # "" means the value check predates this field; treat as "not confirmed"
    # rather than silently passing it as verified.
    return f.value_verification in _VERIFIED_STATES


# --------------------------------------------------------------------------
# Hypothetical alignment — throwaway copies only
# --------------------------------------------------------------------------

def _align(a: Fact, b: Fact, dimension: str) -> Optional[Fact]:
    """Return a COPY of `b` with `dimension` hypothetically aligned to `a`.

    Used only to ask the gate a counterfactual question. The returned fact is
    never surfaced, never stored, and never compared by value — only its
    gate verdict is read. `a` and `b` are not touched: `Qualifiers`,
    `Quantity` and `Period` are all frozen dataclasses, so every change here
    goes through `dataclasses.replace`, which builds a new object.

    Returns None for dimensions that cannot be hypothetically aligned
    without inventing a fact (a value_kind change would make it a different
    claim entirely), which ends the peel.
    """
    q = b.qualifiers
    if dimension == DIM_PERIOD:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, period=a.qualifiers.period))
    if dimension == DIM_SCOPE:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, scope=a.qualifiers.scope))
    if dimension == DIM_SEGMENT:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, segment=a.qualifiers.segment))
    if dimension == DIM_ISSUER:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, issuer=a.qualifiers.issuer))
    if dimension == DIM_BASIS:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, basis=a.qualifiers.basis))
    if dimension == DIM_GEOGRAPHY:
        return dataclasses.replace(b, qualifiers=dataclasses.replace(q, geography=a.qualifiers.geography))
    if dimension == DIM_UNIT:
        if isinstance(a.value, Quantity) and isinstance(b.value, Quantity):
            return dataclasses.replace(
                b, value=dataclasses.replace(b.value, unit=a.value.unit, currency=a.value.currency))
        return None
    return None


def _peel_blocking(a: Fact, b: Fact) -> list[GateResult]:
    """Every gate objection to this pair, in the order the gate raises them.

    See the module docstring: the gate short-circuits, so one call names one
    reason. Each round hypothetically aligns the dimension the gate just
    named and asks again, so the result is the complete objection set — with
    every reason code produced by the real gate, never inferred here.
    """
    out: list[GateResult] = []
    probe = b
    seen: set[str] = set()
    # One round per alignable dimension is the natural ceiling; the explicit
    # bound plus the no-progress guard below make non-termination impossible
    # even if the gate later gains a reason code this module does not know.
    for _ in range(len(DIMENSION_ORDER) + 1):
        g = gate(a, probe)
        if g.verdict == Verdict.COMPARABLE or g.verdict in RELATION_BEARING_VERDICTS:
            break
        if g.reason_code in seen:
            break                       # no progress — stop rather than loop
        seen.add(g.reason_code)
        out.append(g)
        dim = _REASON_TO_DIMENSION.get(g.reason_code)
        if dim is None:
            break
        nxt = _align(a, probe, dim)
        if nxt is None:
            break
        probe = nxt
    return out


# --------------------------------------------------------------------------
# Dimension matrix
# --------------------------------------------------------------------------

def _pair_status(av: Any, bv: Any) -> DimensionStatus:
    """MATCH / MISMATCH / MISSING for a simple optional pair. A dimension no
    source stated is MISSING, never MISMATCH — the distinction §5 requires."""
    if av is None and bv is None:
        return DimensionStatus.MISSING
    if av is None or bv is None:
        return DimensionStatus.MISSING
    return DimensionStatus.MATCH if av == bv else DimensionStatus.MISMATCH


def _build_dimensions(a: Fact, b: Fact) -> list[DimensionReport]:
    both_quantity = isinstance(a.value, Quantity) and isinstance(b.value, Quantity)
    rows: list[DimensionReport] = []

    def add(dim, status, av, bv, detail=""):
        rows.append(DimensionReport(
            dimension=dim, label=DIMENSION_LABELS[dim], status=status,
            fact_a_value=av, fact_b_value=bv, detail=detail,
        ))

    add(DIM_SUBJECT, DimensionStatus.MATCH if a.subject == b.subject else DimensionStatus.MISMATCH,
        a.subject, b.subject,
        "Canonical entity after resolution." if a.subject == b.subject
        else "Different canonical entities; the resolver did not merge these.")

    add(DIM_MEASURE, DimensionStatus.MATCH if a.measure == b.measure else DimensionStatus.MISMATCH,
        a.measure, b.measure)

    add(DIM_VALUE_KIND,
        DimensionStatus.MATCH if a.value_kind == b.value_kind else DimensionStatus.MISMATCH,
        a.value_kind.value, b.value_kind.value)

    pa, pb = _period_label(a), _period_label(b)
    if pa is None or pb is None:
        add(DIM_PERIOD, DimensionStatus.MISSING, pa, pb,
            "At least one fact does not state a reporting period, so the periods "
            "cannot be shown to agree or to differ.")
    else:
        add(DIM_PERIOD, DimensionStatus.MATCH if pa == pb else DimensionStatus.MISMATCH, pa, pb)

    if not both_quantity:
        add(DIM_UNIT, DimensionStatus.NOT_APPLICABLE, _unit_of(a), _unit_of(b),
            "Unit applies only to quantity-valued facts.")
        add(DIM_CURRENCY, DimensionStatus.NOT_APPLICABLE, _currency_of(a), _currency_of(b),
            "Currency applies only to quantity-valued facts.")
    else:
        add(DIM_UNIT, _pair_status(_unit_of(a), _unit_of(b)), _unit_of(a), _unit_of(b))
        ca, cb = _currency_of(a), _currency_of(b)
        if ca is None and cb is None:
            add(DIM_CURRENCY, DimensionStatus.NOT_APPLICABLE, ca, cb,
                "Neither value is denominated in a currency.")
        else:
            add(DIM_CURRENCY, _pair_status(ca, cb), ca, cb,
                "" if ca == cb else
                "This system never applies an FX rate: the correct rate depends on a "
                "reporting date and a rate source, neither of which the documents state.")

    sca, scb = _scope_label(a), _scope_label(b)
    if sca is None or scb is None:
        add(DIM_SCOPE, DimensionStatus.MISSING, sca, scb,
            "Reporting scope is unstated on at least one fact. The system does not "
            "assume consolidated.")
    else:
        add(DIM_SCOPE, DimensionStatus.MATCH if sca == scb else DimensionStatus.MISMATCH, sca, scb,
            "" if sca == scb else
            "Standalone and consolidated figures for the same period can both be correct.")

    add(DIM_ISSUER, _pair_status(a.qualifiers.issuer, b.qualifiers.issuer),
        a.qualifiers.issuer, b.qualifiers.issuer)
    add(DIM_SEGMENT, _pair_status(a.qualifiers.segment, b.qualifiers.segment),
        a.qualifiers.segment, b.qualifiers.segment)
    add(DIM_GEOGRAPHY, _pair_status(a.qualifiers.geography, b.qualifiers.geography),
        a.qualifiers.geography, b.qualifiers.geography)
    add(DIM_BASIS, _pair_status(a.qualifiers.basis, b.qualifiers.basis),
        a.qualifiers.basis, b.qualifiers.basis)

    va, vb = _is_verified(a), _is_verified(b)
    add(DIM_VERIFICATION,
        DimensionStatus.MATCH if (va and vb) else DimensionStatus.UNVERIFIABLE,
        _verification_label(a), _verification_label(b),
        "Both facts are evidence-grounded and their values independently confirmed."
        if (va and vb) else
        "Evidence uncertainty is reported separately from semantic incompatibility: "
        "the comparability gate does not consider verification state, so this never "
        "blocks a comparison by itself.")
    return rows


# --------------------------------------------------------------------------
# Counterfactual readiness
# --------------------------------------------------------------------------

_ACTION_FOR_DIMENSION = {
    DIM_PERIOD: ActionType.ALIGN_PERIOD,
    DIM_SCOPE: ActionType.ALIGN_SCOPE,
    DIM_UNIT: ActionType.ALIGN_UNIT,
    DIM_CURRENCY: ActionType.ALIGN_CURRENCY,
    DIM_ISSUER: ActionType.ALIGN_ISSUER,
    DIM_SEGMENT: ActionType.ALIGN_SEGMENT,
    DIM_GEOGRAPHY: ActionType.ALIGN_GEOGRAPHY,
    DIM_BASIS: ActionType.ALIGN_BASIS,
    DIM_SUBJECT: ActionType.RESOLVE_ENTITY,
    DIM_MEASURE: ActionType.RESOLVE_MEASURE,
    DIM_VALUE_KIND: ActionType.RESOLVE_VALUE_KIND,
}

# Wording rules, per spec §12-§17. Every string is a REQUIREMENT ("must",
# "requires"), never an instruction to change a particular fact — saying
# "use FY2025" would imply which of the two facts is wrong, which the system
# has no basis to decide.
_REQUIRED_CONDITION = {
    DIM_PERIOD: "Both facts must refer to the same reporting period. Neither figure is "
                "wrong; they measure different windows of time.",
    DIM_SCOPE: "Both facts must be reported on the same basis — standalone with "
               "standalone, or consolidated with consolidated. The system does not "
               "prefer either scope.",
    DIM_UNIT: "Both values must be expressed on a common, verified unit basis before "
              "they can be compared.",
    DIM_CURRENCY: "Both values must be denominated in the same currency, or converted "
                  "using a verified rate. No FX rate is stated in the sources, so this "
                  "system will not convert.",
    DIM_ISSUER: "The facts are attributed to different issuers. A valid comparison "
                "requires an issuer relationship that the knowledge layer explicitly "
                "recognises; differing institutions projecting the same quantity is "
                "forecast disagreement, not a factual error.",
    DIM_SEGMENT: "Both facts must describe the same business segment.",
    DIM_GEOGRAPHY: "Both facts must describe the same geography.",
    DIM_BASIS: "Both facts must be on the same reporting basis (for example both "
               "audited, or both unaudited).",
    DIM_SUBJECT: "Both facts must resolve to the same canonical entity with sufficient "
                 "confidence. The resolver leaves an entity unresolved rather than "
                 "risking an incorrect merge.",
    DIM_MEASURE: "Both facts must describe the same canonical measure.",
    DIM_VALUE_KIND: "Both facts must report the same kind of value; a quantity and a "
                    "date are not different answers to one question.",
}

# Dimensions this system will not silently normalise even given the chance.
_UNSAFE_TO_AUTOMATE = {DIM_CURRENCY, DIM_SUBJECT, DIM_ISSUER}


def _effective_unit_dimension(dims: dict[str, DimensionReport], dimension: str) -> str:
    """gate() emits one reason code, `unit_mismatch`, for both a unit
    difference and a currency difference (see comparability._unit_compatible).
    Resolve which one actually differs so the matrix, the blocking list and
    the readiness action all name the SAME dimension — otherwise the panel
    highlights "Unit" while the action talks about currency.

    The gate's reason_code is preserved untouched; only the dimension label
    this module attaches to it is refined."""
    if dimension != DIM_UNIT:
        return dimension
    unit_rep, cur_rep = dims.get(DIM_UNIT), dims.get(DIM_CURRENCY)
    units_agree = unit_rep is None or unit_rep.status != DimensionStatus.MISMATCH
    currency_differs = cur_rep is not None and cur_rep.status == DimensionStatus.MISMATCH
    return DIM_CURRENCY if (units_agree and currency_differs) else DIM_UNIT


def _build_actions(
    a: Fact, b: Fact, blocking: list[tuple[str, str, str]], dims: dict[str, DimensionReport],
) -> list[CounterfactualAction]:
    """One action per dimension an authority actually named. Nothing is
    emitted speculatively: a dimension that merely differs but does not block
    (the gate tolerates it) produces no action."""
    actions: list[CounterfactualAction] = []
    for dimension, reason_code, authority in blocking:
        report = dims.get(dimension)
        action_type = _ACTION_FOR_DIMENSION.get(dimension)
        if action_type is None:
            continue
        actions.append(CounterfactualAction(
            dimension=dimension,
            action_type=action_type,
            current_status=report.status if report else DimensionStatus.MISMATCH,
            fact_a_value=report.fact_a_value if report else None,
            fact_b_value=report.fact_b_value if report else None,
            required_condition=_REQUIRED_CONDITION.get(dimension, ""),
            safe=dimension not in _UNSAFE_TO_AUTOMATE,
            reason_code=reason_code,
            authority=authority,
        ))
    return actions


# --------------------------------------------------------------------------
# Safe conclusion
# --------------------------------------------------------------------------

def _safe_conclusion(verdict: Verdict, blocking_labels: list[str],
                     structurally_blocked: bool, relation_bearing: bool) -> str:
    if structurally_blocked:
        joined = " and ".join(blocking_labels).lower()
        return (f"Comparison blocked before the comparability gate: the facts differ in "
                f"{joined}. They do not describe the same claim, so no relationship was inferred.")
    if relation_bearing:
        if verdict == Verdict.TEMPORAL_SUCCESSION:
            return ("These are point-in-time claims made at different dates. The system treats "
                    "the later statement as updating the earlier one rather than contradicting "
                    "it, and may record a supersession relationship.")
        return ("One period contains the other, so the smaller figure is expected to be a "
                "component of the larger. The system may record an aggregation relationship "
                "rather than a contradiction.")
    if verdict == Verdict.COMPARABLE:
        return ("All comparability checks the gate applies have passed. These facts may proceed "
                "to relationship adjudication.")
    if not blocking_labels:
        return ("Comparison is not permitted by the comparability gate. No relationship was "
                "inferred.")
    if len(blocking_labels) == 1:
        return (f"Comparison blocked because the facts differ in {blocking_labels[0].lower()}. "
                f"The system intentionally did not infer a contradiction.")
    joined = ", ".join(l.lower() for l in blocking_labels[:-1]) + f" and {blocking_labels[-1].lower()}"
    return (f"Comparison blocked because the facts differ in {joined}. No relationship was "
            f"inferred — a difference the system can explain is not a contradiction.")


# --------------------------------------------------------------------------
# Evidence references
# --------------------------------------------------------------------------

def _evidence_ref(f: Fact, role: str) -> dict:
    """A pointer into the EXISTING evidence model — doc/page/span/bbox — so
    the frontend can reuse its evidence viewer. No document path or
    filesystem location is exposed."""
    ev = f.evidence
    if ev is None:
        return {"role": role, "fact_id": f.fact_id, "available": False}
    return {
        "role": role,
        "fact_id": f.fact_id,
        "available": True,
        "doc_id": ev.doc_id,
        "page": ev.page,
        "verbatim_quote": ev.verbatim_quote,
        "char_start": ev.char_start,
        "char_end": ev.char_end,
        "bbox": list(ev.bbox) if ev.bbox else None,
        "verified": ev.verified,
        "value_verification": f.value_verification or None,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def investigate(a: Fact, b: Fact) -> ComparabilityExplanation:
    """Explain whether `a` and `b` may be compared, and if not, what would
    have to be true first.

    Read-only and deterministic: `a` and `b` are never modified, no network
    or LLM call is made, and the same inputs always produce identical output.
    """
    structural = blocking_check(a, b)
    g = gate(a, b)

    dims = _build_dimensions(a, b)
    by_dim = {d.dimension: d for d in dims}

    blocking: list[tuple[str, str, str]] = []      # (dimension, reason_code, authority)

    if not structural.candidate:
        dim = _BLOCKING_TO_DIMENSION.get(structural.reason or "")
        if dim:
            blocking.append((dim, structural.reason, "retrieval.blocking.blocking_check"))
    else:
        for gr in _peel_blocking(a, b):
            dim = _REASON_TO_DIMENSION.get(gr.reason_code)
            if dim is None:
                continue
            blocking.append((_effective_unit_dimension(by_dim, dim),
                             gr.reason_code, "comparability.gate"))

    for dimension, reason_code, _authority in blocking:
        rep = by_dim.get(dimension)
        if rep is not None:
            rep.is_blocking = True
            rep.reason_code = reason_code

    relation_bearing = g.verdict in RELATION_BEARING_VERDICTS
    structurally_blocked = not structural.candidate
    comparable = (g.verdict == Verdict.COMPARABLE) and not structurally_blocked

    blocking_labels = [DIMENSION_LABELS[d] for d, _r, _a in blocking]
    passing = [d.dimension for d in dims
               if d.status == DimensionStatus.MATCH and not d.is_blocking]
    ambiguous = [d.dimension for d in dims
                 if d.status in (DimensionStatus.MISSING, DimensionStatus.AMBIGUOUS,
                                 DimensionStatus.UNVERIFIABLE)]

    caveats: list[str] = []
    verification = by_dim[DIM_VERIFICATION]
    if verification.status == DimensionStatus.UNVERIFIABLE:
        caveats.append(
            "At least one fact's value is not independently confirmed against its evidence. "
            "This does not block comparison — the gate does not consider verification state — "
            "but any conclusion drawn should be treated as provisional.")
    for dim_key in (DIM_PERIOD, DIM_SCOPE):
        rep = by_dim[dim_key]
        if rep.status == DimensionStatus.MISSING and not rep.is_blocking:
            caveats.append(
                f"{rep.label} is unstated on at least one fact, so the gate could not test it. "
                f"Agreement on {rep.label.lower()} has not been established, only left unchecked.")

    return ComparabilityExplanation(
        fact_a_id=a.fact_id,
        fact_b_id=b.fact_id,
        verdict=g.verdict.value,
        comparable=comparable,
        relation_bearing=relation_bearing,
        structurally_blocked=structurally_blocked,
        gate_reason_code=g.reason_code,
        gate_explanation=g.explanation,
        dimensions=dims,
        blocking_reasons=[
            {"dimension": d, "label": DIMENSION_LABELS[d], "reason_code": r, "authority": auth}
            for d, r, auth in blocking
        ],
        passing_dimensions=passing,
        ambiguous_dimensions=ambiguous,
        counterfactual_actions=_build_actions(a, b, blocking, by_dim),
        safe_conclusion=_safe_conclusion(g.verdict, blocking_labels,
                                         structurally_blocked, relation_bearing),
        caveats=caveats,
        evidence_refs=[_evidence_ref(a, "fact_a"), _evidence_ref(b, "fact_b")],
    )


__all__ = [
    "DimensionStatus", "ActionType", "DimensionReport", "CounterfactualAction",
    "ComparabilityExplanation", "investigate", "DIMENSION_ORDER", "DIMENSION_LABELS",
    "RELATION_BEARING_VERDICTS",
]
