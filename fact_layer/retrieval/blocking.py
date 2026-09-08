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

So this module blocks ONLY subject, measure, and value_kind mismatches —
the exact set the gate would turn into `UNRELATED` anyway:

  SUBJECT_MISMATCH    canonical subject differs — never in the same
                       cluster today either (Fact.cluster_key()); this
                       generalizes that existing hard boundary rather
                       than loosening or tightening it.
  MEASURE_MISMATCH    canonical measure differs — same reasoning.
  VALUE_KIND_MISMATCH value_kind differs — mirrors gate()'s own
                       INCOMPARABLE_KIND -> UNRELATED exactly; zero
                       information loss.

AN EARLIER VERSION OF THIS MODULE ALSO BLOCKED UNIT-CATEGORY MISMATCHES
(percent vs. currency vs. count) AS A "NARROW, RARE EDGE CASE." MEASURED
EVIDENCE PROVED THAT WRONG, NOT NARROW:
--------------------------------------------------------------------------
Running the real committed corpus (`data/store.json`, 552 facts, 15 known
relations) through that rule showed it silently discarding **8 of the 15
relations (53%)** — not a rare edge case at all. The reason is structural,
not a tuning mistake: `gate()` checks unit compatibility only *after*
scope, issuer, and segment already matched (or one side left them
unstated) — by the time two facts differ ONLY in reported unit/currency,
`gate()` reaches `INCOMPARABLE_UNIT`, which `adjudicate()` turns into a
real `APPARENT_CONFLICT`/`CORROBORATES`-despite-context relation, exactly
like every other `INCOMPARABLE_*` verdict except `INCOMPARABLE_KIND`.
Blocking on it destroys real relations for the same reason blocking on
scope/segment/geography would — this project's own real financial/macro
corpus reports the same measure in mismatched units often enough that
"unit mismatch" is a common, real signal worth showing through the gate,
not a hypothetical worth trading away for a marginal efficiency gain that
was never actually measured to matter. See README §10a / Honest
Limitations for the full before/after measurement
(`tests/test_retrieval_real_corpus_recall.py` pins the fixed 15/15
recovery as a regression test).

Scope, segment, geography, basis, issuer, and unit/currency mismatches of
every kind are NEVER blocked here — they must reach `gate()` so the
existing CORROBORATES/APPARENT_CONFLICT logic stays fully discoverable
through retrieval. See tests/test_retrieval_blocking.py and
tests/test_retrieval_integration.py for the regression tests proving this
(mirrors the task's own section 22 requirement).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from ..models import Fact


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
    return _PASS


def block_key_fields(fact: Fact) -> tuple[str, str, str]:
    """The raw tuple `blocking_check()` compares, in its comparison order.

    `blocking_check(a, b).candidate` is TRUE if and only if
    `block_key_fields(a) == block_key_fields(b)` — the function is three
    chained equality tests on exactly these three fields and nothing else.
    That equivalence is what makes it safe to apply blocking as a *search
    restriction* (retrieve only within a bucket) instead of as a
    *post-filter* (retrieve globally, then discard): both compute the
    identical predicate, so pre-filtering removes only pairs the
    post-filter would have removed anyway. It is pinned by
    `tests/test_retrieval_blocking.py`'s equivalence test rather than left
    as a comment, because the whole scale argument rests on it.

    Note what is deliberately NOT in this tuple: period, unit, currency,
    scope, segment, geography, issuer, basis. Adding any of them here
    would silently delete real relations (see the 8/15 measurement in this
    module's docstring) — this key must never grow."""
    return (fact.subject, fact.measure, fact.value_kind.value)


def block_key(fact: Fact) -> str:
    """A single opaque token identifying `fact`'s blocking bucket, for
    index backends that need a scalar key rather than a tuple (the FTS5
    lexical column, the vector store's bucket map).

    Hashed rather than concatenated because subject/measure are
    free-form canonical strings that may contain the separator, quotes,
    or FTS5 query metacharacters; a fixed-width hex token is safe to embed
    in an FTS5 MATCH expression and to use as a dict key. Truncated to 16
    hex chars (64 bits) — collision probability across a corpus of even
    10^6 buckets is ~10^-7, and a collision would only ever *widen* a
    bucket (extra candidates that `blocking_check()` then rejects at
    annotation time), never drop a real one, so it cannot cause the
    silent-relation-loss failure mode this module exists to prevent."""
    raw = "\x1f".join(block_key_fields(fact))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


__all__ = ["BlockingResult", "blocking_check", "block_key", "block_key_fields"]
