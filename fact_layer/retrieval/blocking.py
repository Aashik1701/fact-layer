"""
Deterministic candidate blocking — the first, cheapest filter in the
retrieval pipeline, applied before lexical/semantic retrieval even runs.

WHAT THIS IS ALLOWED TO BLOCK, AND WHY IT IS DELIBERATELY NARROWER THAN
THE TASK BRIEF'S ILLUSTRATIVE EXAMPLE LIST
--------------------------------------------------------------------------
`fact_layer.comparability.gate()` turns almost every "incomparable"
verdict into a real, meaningful `Relation` — `CORROBORATES`-despite-context
or `APPARENT_CONFLICT` — via `fact_layer.adjudicate.adjudicate()`'s shared
branch for `INCOMPARABLE_PERIOD`, `INCOMPARABLE_SCOPE`, `INCOMPARABLE_UNIT`,
`INCOMPARABLE_SEGMENT`, `INCOMPARABLE_BASIS` and `INCOMPARABLE_ISSUER`. The
ONE verdict that never produces a kept relation is `INCOMPARABLE_KIND`,
which `adjudicate()` maps straight to `UNRELATED` (discarded everywhere
this codebase reports relations).

Blocking on scope, segment, or geography — as the task's illustrative
examples suggest — would therefore silently delete exactly the relation
type this project's README treats as a headline capability ("Case 3:
Apparent Conflict"), and would violate the task's own overriding rule:
"[retrieval] must not change the meaning of the final relationship
logic." So this module only blocks pairs the gate would turn into
`UNRELATED` (or, in one narrow documented case, a vanishingly unlikely
same-subject/measure-but-different-unit-category pair — see
`UNIT_CATEGORY_MISMATCH` below) anyway:

  SUBJECT_MISMATCH          canonical subject differs — never in the same
                             cluster today either (Fact.cluster_key()); this
                             generalizes that existing hard boundary rather
                             than loosening or tightening it.
  MEASURE_MISMATCH          canonical measure differs — same reasoning.
  VALUE_KIND_MISMATCH       value_kind differs — mirrors gate()'s own
                             INCOMPARABLE_KIND -> UNRELATED exactly; zero
                             information loss.
  UNIT_CATEGORY_MISMATCH    both Quantity, and `.unit` (the category —
                             "percent" | "currency" | "count" — NOT the
                             currency code) differs. Narrow, explicitly
                             accepted trade-off: gate() could in principle
                             still turn this into an APPARENT_CONFLICT if
                             an extraction error reported the same
                             subject/measure/period once as a percentage
                             and once as an absolute count. This is the
                             same kind of documented, evidence-weighed
                             trade-off resolve.py already makes elsewhere
                             (see its "why subjects don't get a fuzzy
                             tier" docstring) rather than an oversight.

Scope, segment, geography, basis, issuer and same-category currency
mismatches are NEVER blocked here — they must reach `gate()` so the
existing CORROBORATES/APPARENT_CONFLICT logic stays fully discoverable
through retrieval. See tests/test_retrieval_blocking.py and
tests/test_retrieval_integration.py for the regression tests proving this
(mirrors the task's own section 22 requirement).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Fact, Quantity


@dataclass(frozen=True)
class BlockingResult:
    candidate: bool
    reason: Optional[str] = None   # None when candidate is True

    def to_dict(self) -> dict:
        return {"candidate": self.candidate, "reason": self.reason}


_PASS = BlockingResult(candidate=True, reason=None)


def blocking_check(a: Fact, b: Fact) -> BlockingResult:
    """Cheap, deterministic pre-filter. Returns candidate=True ("not
    obviously impossible, let it through to gate()") for everything except
    the narrow set of pairs documented in this module's docstring."""
    if a.subject != b.subject:
        return BlockingResult(False, "SUBJECT_MISMATCH")
    if a.measure != b.measure:
        return BlockingResult(False, "MEASURE_MISMATCH")
    if a.value_kind != b.value_kind:
        return BlockingResult(False, "VALUE_KIND_MISMATCH")
    if isinstance(a.value, Quantity) and isinstance(b.value, Quantity):
        if a.value.unit != b.value.unit:
            return BlockingResult(False, "UNIT_CATEGORY_MISMATCH")
    return _PASS


__all__ = ["BlockingResult", "blocking_check"]
