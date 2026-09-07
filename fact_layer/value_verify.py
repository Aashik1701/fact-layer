"""
Deterministic value verification.

Span verification (extract.py) proves `verbatim_quote` occurs in the source
PDF. It does NOT prove that `value_raw` — a separate field the LLM emitted in
the same JSON object — is the number actually written in that quote. An LLM
can quote real text correctly while misreporting the figure next to it (wrong
column, wrong row, a transposed digit). This module closes that specific gap
with pure, deterministic Python — no LLM call.

Reuses, never reimplements: `normalize.parse_quantity` for every numeric
parse (value_raw's own and every candidate found in the quote) and
`normalize.parse_period` to keep date/FY phrases from being mistaken for
value candidates, and the same sig-fig-aware tolerance style
`adjudicate._values_agree` uses to decide whether two facts' values
corroborate.

Unit/currency compatibility is deliberately NOT `comparability._unit_compatible`
(reused verbatim it makes the extraction pipeline's own asymmetry a false
positive): value_raw's own quantity is parsed from `effective_context`
(table scale/measure text), while a candidate read out of the quote can pick
up a currency symbol sitting right next to the digits that never made it
into `effective_context`. Comparing two FACTS to each other, either genuinely
carries a unit or it doesn't; comparing a fact to its OWN evidence, one side
having strictly *more* information than the other is normal and must not be
mistaken for the two disagreeing. Only a CONCRETE conflict — both sides name
a currency and it differs, or one side is a percent and the other plainly
isn't — blocks the comparison; the same no-silent-FX-conversion principle as
the gate, applied at the point information is actually incomplete rather
than a raw label mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .adjudicate import _values_agree
from .models import Quantity
from .normalize import (
    _ASON_RE,
    _DATE_PATTERNS,
    _ENDED_RE,
    _FY_RE,
    _FY_SINGLE_RE,
    _NUM_RE,
    _QTR_RE,
    SCALES,
    parse_period,
    parse_quantity,
)

_SCALE_MULTIPLIERS = set(SCALES.values())

_PERIOD_REGIONS = [_FY_RE, _FY_SINGLE_RE, _QTR_RE, _ENDED_RE, _ASON_RE] + _DATE_PATTERNS

# normalize.py's own FY regexes require a 4-digit year (or an explicit
# YYYY-YY range) and so don't recognise "FY24" — a real, common short form
# the project specification worked examples use. parse_period() genuinely can't turn
# that into a Period (no century to anchor it to), so masking can't route
# through it the way _mask_period_phrases() does for everything else; this
# narrower rule only has to know "digits right after FY/F.Y. are a fiscal
# year label, not a value", which holds regardless of digit count.
_BARE_FY_RE = re.compile(r"\bf\.?y\.?\s*[:\-]?\s*\d{2,4}\b", re.I)


class ValueVerificationStatus(str, Enum):
    VERIFIED = "verified"       # value_raw's number is the sole number the quote supports
    UNVERIFIED = "unverified"   # quote doesn't let us deterministically confirm OR deny it
    MISMATCH = "mismatch"       # quote unambiguously supports a DIFFERENT number


@dataclass(frozen=True)
class ValueVerification:
    status: ValueVerificationStatus
    reason: str                        # machine-readable code, for audit/API
    extracted: Optional[str] = None    # value_raw's own parsed value (canonical, base units)
    evidence: Optional[str] = None     # the quote-derived value(s) it was checked against


def _mask_period_phrases(text: str) -> str:
    """Blank out substrings that are genuinely period/date phrases (FY2024,
    Q1 FY25, "as on 31 March 2024", ...) so their embedded digits are never
    mistaken for a value candidate. A regex hit alone isn't trusted — it must
    also round-trip through the real `parse_period()` — so a superficial
    look-alike (no valid date inside it) is left unmasked rather than
    silently dropped."""
    masked = list(text)
    for pat in _PERIOD_REGIONS:
        for m in pat.finditer(text):
            if parse_period(m.group(0)) is None:
                continue
            for i in range(m.start(), m.end()):
                masked[i] = " "
    for m in _BARE_FY_RE.finditer(text):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    return "".join(masked)


def _scale_asymmetry_explains_gap(a: Quantity, b: Quantity) -> bool:
    """True when the two magnitudes differ by EXACTLY one of the known
    scale multipliers (lakh/crore/million/...) — reusing normalize.SCALES,
    never a second multiplier table. This is the real, observed signature of
    a scale word (e.g. "million") sitting in the quote but not in
    `effective_context` (or vice versa), so value_raw's own parse and the
    quote's candidate land on the same digits at two different scales — not
    evidence of a genuinely different figure."""
    if a.value == 0 or b.value == 0:
        return False
    hi, lo = (a.value, b.value) if abs(a.value) >= abs(b.value) else (b.value, a.value)
    return (hi / lo) in _SCALE_MULTIPLIERS


def _candidate_quantities(quote: str, context: str) -> list[Quantity]:
    """Every distinct numeric quantity the quote text can support, using the
    SAME parse_quantity() the real pipeline uses for value_raw itself — never
    a second numeric parser. `context` is the same effective scale/currency
    context extract.py already resolved for this fact (table scale_context +
    measure_raw), applied identically here so a candidate found only in the
    quote and value_raw's own parse are compared on the same basis, not
    guessed independently."""
    # normalize.parse_quantity() itself replaces the Unicode minus sign
    # (U+2212, common in IMF/typeset PDF text — pdfplumber preserves it
    # verbatim) with an ASCII '-' before matching _NUM_RE, so a negative
    # value_raw like "−0.4 percent" parses to -0.4. _NUM_RE only matches
    # ASCII '-', so scanning the quote for candidates needs the identical
    # replacement, or "−0.4" in the quote silently becomes candidate 0.4
    # (sign dropped) and a correct negative value_raw looks like a mismatch.
    usable = _mask_period_phrases(quote.replace("−", "-"))
    seen: set[str] = set()
    out: list[Quantity] = []
    for m in _NUM_RE.finditer(usable):
        literal = m.group(0)
        # A bare '%' immediately after the digits is part of THIS number's
        # own literal, not of `context` — parse_quantity only checks for '%'
        # inside the literal it's given, so it must travel with it here too.
        tail = usable[m.end():m.end() + 2]
        if tail.lstrip().startswith("%"):
            literal += "%"
        q = parse_quantity(literal, context=f"{quote} {context}")
        if q is None:
            continue
        key = f"{q.value}|{q.unit}|{q.currency}"
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def verify_value(
    value_raw: str,
    quote: str,
    quantity: Optional[Quantity],
    context: str = "",
) -> ValueVerification:
    """Check whether `quantity` (already parsed from `value_raw` by the real
    pipeline) is the value the span-verified `quote` actually supports.

    Non-numeric facts (quantity is None — a text/entity/date claim) have no
    deterministic numeric check to run; they are UNVERIFIED, never VERIFIED
    by this function, and never MISMATCH — there is nothing here that could
    safely prove disagreement either.
    """
    if quantity is None:
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "non_numeric_value_no_deterministic_verifier",
        )

    candidates = _candidate_quantities(quote, context)
    if not candidates:
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "quote_contains_no_numeric_literal",
            extracted=str(quantity.value),
        )

    distinct_values = {str(c.value) for c in candidates}
    if len(distinct_values) > 1:
        # The core project principle applies here too: never compare (or
        # bless) a value before establishing which quoted number it even
        # refers to. A table row with several candidate numbers and no
        # column/position information in the evidence model is a genuine
        # ambiguity, not a license to pick the one that happens to match.
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "multiple_numeric_candidates_in_quote_ambiguous",
            extracted=str(quantity.value),
            evidence=", ".join(sorted(distinct_values)),
        )

    candidate = candidates[0]
    extracted, evidence = str(quantity.value), str(candidate.value)

    # Percent is a distinct semantic category regardless of magnitude — a
    # "6.5" read as a plain count is not confirmation of a "6.5%" claim.
    if (quantity.unit == "percent") != (candidate.unit == "percent"):
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "percent_vs_non_percent_ambiguous", extracted, evidence,
        )

    # Never silently reconcile two DIFFERENT named currencies — same
    # no-FX-conversion rule the comparability gate applies between facts.
    # One side simply lacking a currency marker is not this: it is the
    # normal case where the scale/currency phrase lives in a table header
    # (effective_context) rather than beside the digits in the quote.
    if quantity.currency and candidate.currency and quantity.currency != candidate.currency:
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            f"currency_mismatch_no_fx_conversion ({quantity.currency} vs {candidate.currency})",
            extracted, evidence,
        )

    agree, _diff = _values_agree(quantity, candidate)
    if agree:
        return ValueVerification(
            ValueVerificationStatus.VERIFIED,
            "value_matches_sole_quote_number", extracted, evidence,
        )

    if bool(quantity.currency) != bool(candidate.currency):
        # Magnitudes disagree AND currency information is only available on
        # one side — could be a real mismatch or an incomplete parse on the
        # bare side; not safe to call it either way.
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "incomplete_currency_context_magnitude_differs", extracted, evidence,
        )
    if _scale_asymmetry_explains_gap(quantity, candidate):
        return ValueVerification(
            ValueVerificationStatus.UNVERIFIED,
            "scale_context_asymmetry_same_digits_different_scale", extracted, evidence,
        )
    return ValueVerification(
        ValueVerificationStatus.MISMATCH,
        "value_disagrees_with_sole_quote_number", extracted, evidence,
    )
