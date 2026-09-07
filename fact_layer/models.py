"""
Core data model for the Fact Knowledge Layer.

Design note (read this before changing anything):
    A fact is NOT a (subject, predicate, object) triple.
    A fact is a CLAIM PLUS ITS QUALIFIERS.

    Two facts about "revenue" are only in conflict if they refer to the same
    subject, the same measure, AND their qualifiers are compatible. Almost every
    naive fact-extraction system fails because it compares values before
    checking comparability. Everything in this package exists to make that
    check explicit, deterministic and explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
import hashlib
import json


# --------------------------------------------------------------------------
# Value types
# --------------------------------------------------------------------------

class ValueKind(str, Enum):
    QUANTITY = "quantity"      # 120.4 crore INR, 12.5 %
    DATE = "date"              # 2025-03-12
    ENTITY = "entity"          # "Acme Technologies Private Limited"
    TEXT = "text"              # free-form string claim
    BOOLEAN = "boolean"        # is_active = true


@dataclass(frozen=True)
class Quantity:
    """A number with unit, currency and — critically — its stated precision.

    `sig_figs` is inferred from how the number was WRITTEN in the source, not
    from its magnitude. "Rs. 120.4 Cr" carries 4 significant figures, so it is
    only asserting the value to within +/- 0.05 Cr. Comparing it against an
    exact figure of Rs. 1,20,41,23,456 must therefore be a CORROBORATION, not a
    contradiction. Rounding-awareness is what stops the system crying wolf.
    """
    value: Decimal                      # always normalised to base units
    unit: str = "count"                 # "currency" | "percent" | "count" | "person" ...
    currency: Optional[str] = None      # "INR" | "USD" | None
    sig_figs: int = 15                  # precision as stated in the source
    raw: str = ""                       # verbatim literal, e.g. "Rs. 120.4 Cr"

    def tolerance(self) -> Decimal:
        """Absolute tolerance implied by the stated precision."""
        if self.value == 0:
            return Decimal("0")
        magnitude = abs(self.value)
        # position of the last significant digit
        exp = magnitude.adjusted() - (self.sig_figs - 1)
        return Decimal(10) ** exp / 2


class PeriodKind(str, Enum):
    INSTANT = "instant"     # "as on 31 March 2025" — a stock/point-in-time value
    DURATION = "duration"   # "FY2023-24" — a flow value over a window


@dataclass(frozen=True)
class Period:
    kind: PeriodKind
    start: Optional[date] = None
    end: Optional[date] = None
    label: str = ""         # verbatim, e.g. "quarter ended 30 June 2025"

    def as_instant(self) -> Optional[date]:
        return self.end if self.kind == PeriodKind.INSTANT else None


class Scope(str, Enum):
    """Reporting boundary. Standalone and consolidated figures for the same
    period are BOTH correct and are not in conflict — a fact the naive
    numeric-diff approach gets wrong every time."""
    STANDALONE = "standalone"
    CONSOLIDATED = "consolidated"
    SEGMENT = "segment"
    UNKNOWN = "unknown"


class Modality(str, Enum):
    ASSERTED = "asserted"
    ESTIMATED = "estimated"
    PROJECTED = "projected"
    RESTATED = "restated"     # supersedes an earlier asserted value by design
    NEGATED = "negated"


@dataclass(frozen=True)
class Qualifiers:
    period: Optional[Period] = None
    as_of: Optional[date] = None        # when the claim was true / reported
    scope: Scope = Scope.UNKNOWN
    basis: Optional[str] = None         # "audited" | "unaudited" | "provisional"
    segment: Optional[str] = None       # business/product segment
    geography: Optional[str] = None
    issuer: Optional[str] = None        # who is making the claim (IMF, RBI, ...) — required for macro data
    # Open-ended qualifier bag: a place for a kind of qualifier the fixed
    # fields above don't name, so a novel one is captured rather than
    # silently dropped. Not currently populated by extract.py — wiring the
    # extraction prompt to propose keys here would change the LLM call and
    # invalidate the committed replay cache (cache keys are sha256 of the
    # exact prompt text), so this is deliberately schema-only for now: the
    # capability exists and diff()/serialization already handle it, extraction
    # wiring is a separate, higher-risk change. See README Limitations.
    extra: dict[str, Any] = field(default_factory=dict)

    def diff(self, other: "Qualifiers") -> dict[str, tuple[Any, Any]]:
        """Which qualifiers differ — this drives the 'why' shown in the UI."""
        out: dict[str, tuple[Any, Any]] = {}
        for f in ("period", "as_of", "scope", "basis", "segment", "geography", "issuer"):
            a, b = getattr(self, f), getattr(other, f)
            if isinstance(a, Period) or isinstance(b, Period):
                a = a.label if a else None
                b = b.label if b else None
            if a != b:
                out[f] = (a, b)
        for k in sorted(set(self.extra) | set(other.extra)):
            a, b = self.extra.get(k), other.extra.get(k)
            if a != b:
                out[f"extra.{k}"] = (a, b)
        return out


# --------------------------------------------------------------------------
# Evidence — every fact must be able to point at the pixels it came from
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    doc_id: str
    page: int                       # 1-indexed
    char_start: int
    char_end: int
    verbatim_quote: str             # MUST occur in the page text; enforced
    bbox: Optional[tuple[float, float, float, float]] = None
    extractor: str = "llm"          # "llm" | "table" | "regex"
    verified: bool = False          # set True only by the span verifier


# --------------------------------------------------------------------------
# Fact
# --------------------------------------------------------------------------

@dataclass
class Fact:
    subject: str                    # canonical entity id
    measure: str                    # canonical attribute id, e.g. revenue_from_operations
    value_kind: ValueKind
    value: Any                      # Quantity | date | str | bool
    qualifiers: Qualifiers = field(default_factory=Qualifiers)
    modality: Modality = Modality.ASSERTED
    evidence: Optional[Evidence] = None
    confidence: float = 1.0
    subject_raw: str = ""           # pre-canonicalisation, kept for audit
    measure_raw: str = ""
    fact_id: str = ""

    def __post_init__(self) -> None:
        if not self.fact_id:
            self.fact_id = self.compute_id()

    def compute_id(self) -> str:
        ev = self.evidence
        seed = json.dumps(
            {
                "s": self.subject,
                "m": self.measure,
                "v": str(self.value),
                "d": ev.doc_id if ev else "",
                "p": ev.page if ev else -1,
                "c": ev.char_start if ev else -1,
            },
            sort_keys=True,
        )
        return "f_" + hashlib.sha1(seed.encode()).hexdigest()[:12]

    def cluster_key(self) -> str:
        """Facts sharing a cluster key are ABOUT the same thing and are worth
        comparing. Note this deliberately excludes qualifiers — qualifier
        compatibility is decided later, by the comparability gate."""
        return f"{self.subject}::{self.measure}"

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.value, Quantity):
            d["value"] = {
                "value": str(self.value.value),
                "unit": self.value.unit,
                "currency": self.value.currency,
                "sig_figs": self.value.sig_figs,
                "raw": self.value.raw,
            }
        elif isinstance(self.value, date):
            d["value"] = self.value.isoformat()
        return d


# --------------------------------------------------------------------------
# Relations between facts
# --------------------------------------------------------------------------

class RelationType(str, Enum):
    CORROBORATES = "corroborates"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"                      # later as_of replaces earlier
    APPARENT_CONFLICT = "apparent_conflict"        # differs, but explained by context
    AGGREGATES_INTO = "aggregates_into"            # Q1..Q4 -> FY
    UNRELATED = "unrelated"


@dataclass
class Relation:
    source_fact_id: str
    target_fact_id: str
    relation: RelationType
    confidence: float
    reason_code: str                # machine-readable, e.g. "period_disjoint"
    explanation: str                # human sentence shown in the UI
    qualifier_diff: dict = field(default_factory=dict)
    decided_by: str = "rule"        # "rule" | "llm"
