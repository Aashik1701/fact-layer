"""Tests for fact_layer/value_verify.py — deterministic value verification.

Span verification (extract.py) proves a quote occurs in the PDF; it does not
prove value_raw is the number that quote actually supports. These tests cover
the worked examples from the correctness-hardening brief: scale-equivalent
numbers, percentages, Indian accounting formats, ambiguous multi-number
quotes, and unit/currency safety (no silent FX conversion).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fact_layer.normalize import parse_quantity
from fact_layer.value_verify import ValueVerificationStatus, verify_value


def _verify(value_raw, quote, context=""):
    q = parse_quantity(value_raw, context)
    return verify_value(value_raw, quote, q, context)


# --------------------------------------------------------------------------
# A1.4 — numeric value verification
# --------------------------------------------------------------------------

def test_matching_crore_value_is_verified():
    r = _verify("120.4 crore", "Revenue from operations reached ₹120.4 crore in FY24")
    assert r.status is ValueVerificationStatus.VERIFIED


def test_matching_percent_value_is_verified():
    r = _verify("6.5%", "India's real GDP grew by 6.5 percent in FY2024/25.")
    assert r.status is ValueVerificationStatus.VERIFIED


def test_disagreeing_single_number_quote_is_mismatch():
    """Both sides concretely name the same currency (INR) — the only thing
    that differs is the magnitude, which is exactly the safe-to-reject case."""
    r = _verify("₹120.4 crore", "Revenue from operations reached ₹145.9 crore in FY24.")
    assert r.status is ValueVerificationStatus.MISMATCH
    assert r.extracted == "1204000000.0"
    assert r.evidence == "1459000000.0"


# --------------------------------------------------------------------------
# A1.5 — scale normalisation reuses normalize.py, no currency conversion
# --------------------------------------------------------------------------

def test_crore_and_lakh_phrasing_both_verify_against_their_own_quote():
    r1 = _verify("120.4 crore", "The company reported Rs. 120.4 Cr for the year.")
    assert r1.status is ValueVerificationStatus.VERIFIED
    # Scale here lives in the table's own context (mirrors extract.py's
    # scale_context, e.g. a "Rs. in lakhs" table caption), not repeated
    # beside the digits in the quote — the realistic shape for this corpus.
    r2 = _verify("12,040", "Revenue was 12,040 for the year.", context="Rs. in lakhs")
    assert r2.status is ValueVerificationStatus.VERIFIED


def test_currency_mismatch_is_unverified_not_silently_reconciled():
    """INR vs USD must never be treated as comparable by guessing an FX
    rate — same invariant comparability.py enforces between two facts,
    enforced here between a fact and its own evidence."""
    r = _verify("120.4 crore", "The equivalent figure was reported as US$ 120.4 million.")
    assert r.status is ValueVerificationStatus.UNVERIFIED
    assert "currency" in r.reason


# --------------------------------------------------------------------------
# A1.6 — percentages are not confused with their decimal form
# --------------------------------------------------------------------------

def test_percent_word_form_verifies():
    r = _verify("6.5 percent", "The rate stood at 6.5 percent for the quarter.")
    assert r.status is ValueVerificationStatus.VERIFIED


def test_percent_vs_bare_decimal_is_unit_mismatch_not_guessed_equal():
    """value_raw states 6.5% but the quote's sole number has no percent
    marker anywhere (so parse_quantity reads it as a bare count) — this must
    not be silently treated as the same as 0.065 or 6.5."""
    r = _verify("6.5%", "The reading was 0.065 for the quarter.")
    assert r.status is ValueVerificationStatus.UNVERIFIED


# --------------------------------------------------------------------------
# A1.7 — Indian accounting formats reuse the existing parser
# --------------------------------------------------------------------------

def test_indian_grouping_format_verifies():
    r = _verify("1,20,41,23,456", "The exact balance was 1,20,41,23,456 as reported.")
    assert r.status is ValueVerificationStatus.VERIFIED


# --------------------------------------------------------------------------
# A1.8 — ambiguous multi-number quotes must not be guessed
# --------------------------------------------------------------------------

def test_multiple_numbers_in_quote_is_unverified_even_if_one_matches():
    """The quote contains three candidate numbers with no column/row
    information distinguishing them; value_raw happening to equal the FIRST
    one is not proof it's the right one."""
    r = _verify("120.4", "Revenue 120.4 145.9 132.1")
    assert r.status is ValueVerificationStatus.UNVERIFIED
    assert r.reason == "multiple_numeric_candidates_in_quote_ambiguous"


def test_real_esops_two_plan_row_is_unverified_not_guessed():
    """Real corpus case (delhivery annual report p.43): one table row
    reports two different ESOP plans' vesting counts in a single quoted
    span with no column label attached. Neither number should be blessed as
    'the' value."""
    r = _verify("676,000", "No. of ESOPs vested as on - 676,000 - 250,000")
    assert r.status is ValueVerificationStatus.UNVERIFIED


# --------------------------------------------------------------------------
# A1.9 — derived values: only a literally-stated number is ever VERIFIED
# --------------------------------------------------------------------------

def test_llm_invented_arithmetic_not_reproducible_from_quote_is_unverified():
    """value_raw claims a subtraction result the quote's own numbers do not
    state directly; since there is no derivation mechanism in this pipeline,
    Python cannot reproduce 426000 from the quote, so it must not be blessed
    as VERIFIED. The quote's own numbers (676000, 250000) are picked up as
    ambiguous candidates instead — never silently accepted as equal to
    value_raw's invented total."""
    r = _verify("426,000", "Vested 676,000 minus exercised 250,000")
    assert r.status is not ValueVerificationStatus.VERIFIED


# --------------------------------------------------------------------------
# A1.10 — non-numeric facts get UNVERIFIED, never VERIFIED by numeric logic
# --------------------------------------------------------------------------

def test_text_claim_is_unverified_never_verified():
    r = verify_value("active", "Mr. R Krishnan continues as Director, status: active.", None)
    assert r.status is ValueVerificationStatus.UNVERIFIED
    assert r.reason == "non_numeric_value_no_deterministic_verifier"


# --------------------------------------------------------------------------
# Dates/FY phrases inside the same quote must not masquerade as candidates
# --------------------------------------------------------------------------

def test_fy_year_inside_quote_is_not_mistaken_for_a_second_candidate():
    """"...for FY2024." must not add "2024" as a second, distinct numeric
    candidate — if it did, this would wrongly fall into the
    multiple-candidates-ambiguous UNVERIFIED path instead of verifying."""
    r = _verify("8,141.71 crore", "Revenue from operations was Rs. 8,141.71 Cr for FY2024.")
    assert r.status is ValueVerificationStatus.VERIFIED
    assert r.reason == "value_matches_sole_quote_number"


def test_bare_two_digit_fy_inside_quote_is_not_mistaken_for_a_second_candidate():
    """normalize.py's FY regexes require a 4-digit year and so can't parse
    the short form "FY24" into a Period — this is a narrower, local rule
    that only has to know digits right after FY are a year label, not a
    value, regardless of digit count."""
    r = _verify("120.4 crore", "Revenue from operations reached ₹120.4 crore in FY24")
    assert r.status is ValueVerificationStatus.VERIFIED


def test_explicit_date_inside_quote_is_not_mistaken_for_a_second_candidate():
    r = _verify("120.4 crore", "As on 31 March 2024, revenue was 120.4 crore.")
    assert r.status is ValueVerificationStatus.VERIFIED


# --------------------------------------------------------------------------
# No numeric literal in the quote at all
# --------------------------------------------------------------------------

def test_quote_with_no_digits_is_unverified():
    r = _verify("120.4 crore", "Revenue grew substantially during the period under review.")
    assert r.status is ValueVerificationStatus.UNVERIFIED
    assert r.reason == "quote_contains_no_numeric_literal"


# --------------------------------------------------------------------------
# Real corpus findings (found via a full 6-document offline re-ingest;
# both were verifier bugs, not genuine hallucinations, and are pinned down
# here so they cannot regress)
# --------------------------------------------------------------------------

def test_unicode_minus_sign_in_quote_is_not_dropped():
    """Real case: IMF Article IV p.52. pdfplumber preserves the Unicode
    minus U+2212 verbatim; normalize.parse_quantity() already replaces it
    with ASCII '-' before matching, so value_raw parses to -0.4 — the quote
    candidate scan must apply the identical replacement or the sign silently
    drops, making a correct negative value look like a mismatch."""
    r = _verify(
        "−0.4 percent of GDP",
        "The EBA cyclically adjusted CA balance is projected at −0.4 percent of GDP in FY2024/25.",
    )
    assert r.status is ValueVerificationStatus.VERIFIED


def test_scale_word_only_in_quote_is_unverified_not_mismatch():
    """Real case: Delhivery annual report p.99, value_raw="716.04" against
    quote "...716.04 million)" with no scale_context passed through
    (effective_context is empty in this call, mirroring the real extraction
    call for this fact). value_raw's own parse has no way to see "million"
    (that word lives only in the quote), so the two numbers land at 716.04
    vs 716,040,000 — a difference explained EXACTLY by one scale multiplier,
    not sixteen different underlying data points. This is an incomplete
    parse of value_raw's own context, not a disagreement with the quote —
    UNVERIFIED, not a rejection."""
    r = _verify("716.04", "(March 31, 2024: I 716.04 million)")
    assert r.status is ValueVerificationStatus.UNVERIFIED
    assert r.reason == "scale_context_asymmetry_same_digits_different_scale"
