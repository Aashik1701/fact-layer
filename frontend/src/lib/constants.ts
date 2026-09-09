import { RelationType } from '@/types';

export const RELATION_CONFIG: Record<
  RelationType,
  {
    label: string;
    description: string;
    color: string;
    bgBadge: string;
    textBadge: string;
    borderBadge: string;
    hex: string;
  }
> = {
  CORROBORATES: {
    label: 'Corroborates',
    description: 'Compatible values under identical or aligned qualifiers and modality.',
    color: 'emerald',
    bgBadge: 'bg-emerald-500/10 hover:bg-emerald-500/20',
    textBadge: 'text-emerald-700 dark:text-emerald-400',
    borderBadge: 'border-emerald-500/30',
    hex: '#10b981',
  },
  CONTRADICTS: {
    label: 'Contradicts',
    description: 'Identical scope and period, but mutually exclusive values (adjudication halves confidence to 0.50).',
    color: 'rose',
    bgBadge: 'bg-rose-500/10 hover:bg-rose-500/20',
    textBadge: 'text-rose-700 dark:text-rose-400',
    borderBadge: 'border-rose-500/30',
    hex: '#f43f5e',
  },
  APPARENT_CONFLICT: {
    label: 'Apparent Conflict',
    description: 'Values appear contradictory but underlying qualifiers (period, scope, modality) differ.',
    color: 'amber',
    bgBadge: 'bg-amber-500/10 hover:bg-amber-500/20',
    textBadge: 'text-amber-800 dark:text-amber-400',
    borderBadge: 'border-amber-500/30',
    hex: '#f59e0b',
  },
  SUPERSEDES: {
    label: 'Supersedes',
    description: 'A revised or finalized metric updates an earlier preliminary estimate.',
    color: 'sky',
    bgBadge: 'bg-sky-500/10 hover:bg-sky-500/20',
    textBadge: 'text-sky-700 dark:text-sky-400',
    borderBadge: 'border-sky-500/30',
    hex: '#0ea5e9',
  },
  AGGREGATES_INTO: {
    label: 'Aggregates Into',
    description: 'A subgroup or component contributes hierarchically to a composite total.',
    color: 'violet',
    bgBadge: 'bg-violet-500/10 hover:bg-violet-500/20',
    textBadge: 'text-violet-700 dark:text-violet-400',
    borderBadge: 'border-violet-500/30',
    hex: '#8b5cf6',
  },
};

// Keyed on the ACTUAL reason_code strings fact_layer/comparability.py and
// fact_layer/adjudicate.py produce (verified against the real backend
// source, not guessed) - a previous version of this table used invented,
// never-matching codes (e.g. "LOW_OCR_CONFIDENCE", on a system with no OCR
// at all), so every relation silently fell through to a generic fallback
// with no user-visible error. One of adjudicate.py's reason codes is
// DYNAMIC, not literal, and is handled separately in getCaveatExplanation:
//   "value_match_despite_<gate_reason_code>" - a corroboration whose gate
//     verdict was actually incomparable for some reason, composited in.
// (A second dynamic suffix, "<reason_code>_period_unverified", existed here
// until the source-of-truth fix: an unstated/unparseable period used to let
// gate() return COMPARABLE anyway, so adjudicate.py flagged the resulting
// relation's confidence as unverified after the fact. gate() now returns
// Verdict.AMBIGUOUS with reason_code "ambiguous_period" BEFORE any relation
// is produced, so that suffix can no longer occur - see comparability.py's
// UNKNOWN-fallthrough audit note and adjudicate.py's history for both.)
export const REASON_CODE_CAVEATS: Record<string, string> = {
  comparable: 'Same subject, measure, scope, unit and period - a direct like-for-like comparison.',
  value_match: 'Both sources state the same value on a like-for-like basis.',
  value_mismatch: 'Same subject, measure, scope and period, but the reported values genuinely differ.',
  restated_unchanged: 'A later point-in-time statement restates the same value as an earlier one.',
  temporal_succession: 'These are point-in-time claims made at different dates; the later statement updates rather than contradicts the earlier one.',
  period_disjoint: 'Reported periods do not overlap at all (e.g. FY24 vs FY25) - the figures measure different windows of time.',
  period_overlap: 'Reported periods partially overlap, so the figures are not on a strictly like-for-like basis.',
  period_subsumption: 'One period contains the other (e.g. a quarter within its fiscal year) - the smaller figure is expected to be a component, not equal to it.',
  ambiguous_period: 'At least one fact’s reporting period is unstated, or could not be parsed to an actual date - the gate cannot verify whether the periods agree, so no comparison was made.',
  scope_mismatch: 'Reported on different bases (e.g. standalone vs consolidated) - both figures can be correct for the same period.',
  segment_mismatch: 'The figures refer to different business or product segments.',
  unit_mismatch: 'Units or currencies differ, and no exchange rate is stated in either source - never silently converted.',
  basis_mismatch: 'One figure is audited and the other unaudited - a revision is expected, not a contradiction.',
  value_kind_mismatch: 'One side is a quantity and the other a different kind of claim entirely - not numerically comparable.',
  forecast_disagreement: 'Different institutions project different values for the same period - a forecast disagreement between sources, not a factual error.',
};

const _GENERIC_FALLBACK = 'Contextual qualifier difference detected by the comparability gate.';

export function getCaveatExplanation(reasonCode: string): string {
  if (!reasonCode) return _GENERIC_FALLBACK;
  if (reasonCode in REASON_CODE_CAVEATS) return REASON_CODE_CAVEATS[reasonCode];

  if (reasonCode.startsWith('value_match_despite_')) {
    const inner = reasonCode.slice('value_match_despite_'.length);
    const innerCaveat = REASON_CODE_CAVEATS[inner];
    return innerCaveat
      ? `Values agree even though: ${innerCaveat}`
      : 'Values agree despite a contextual difference the gate flagged.';
  }
  return _GENERIC_FALLBACK;
}

// Keyed on the ACTUAL reason strings fact_layer/value_verify.py's
// ValueVerification.reason produces (verified against the real backend
// source). "No value was guessed" is the operative product principle this
// whole table exists to make visible: every one of these explanations
// describes what the pipeline deliberately did NOT do, not a failure being
// smoothed over.
const VALUE_VERIFICATION_EXPLANATIONS: Record<string, string> = {
  non_numeric_value_no_deterministic_verifier:
    'This is a text/entity claim, not a number - there is no deterministic numeric check to run against it. Unverified here means "not checked", not a quality warning.',
  quote_contains_no_numeric_literal:
    'The cited source region contains no numeric value at all to check the extracted figure against.',
  multiple_numeric_candidates_in_quote_ambiguous:
    'Multiple numeric candidates exist in the cited source region, and the available document structure is insufficient to determine which value represents the fact. No value was guessed.',
  percent_vs_non_percent_ambiguous:
    'The source expresses this figure differently as a percentage on one side and a plain number on the other - treating them as the same value would require an assumption the source does not state.',
  incomplete_currency_context_magnitude_differs:
    'The extracted value and the cited source disagree in magnitude, and currency information is only available on one side - this could be a real disagreement or an incomplete read, so it is not resolved either way.',
  scale_context_asymmetry_same_digits_different_scale:
    'The same digits appear on both sides but a scale word (e.g. "million") is only available in one context - resolved as unverified rather than assuming which scale applies.',
};

export function getValueVerificationExplanation(reason: string): string {
  if (!reason) return 'The available document structure was insufficient to confirm this value deterministically. No value was guessed.';
  if (reason in VALUE_VERIFICATION_EXPLANATIONS) return VALUE_VERIFICATION_EXPLANATIONS[reason];
  if (reason.startsWith('currency_mismatch_no_fx_conversion')) {
    return 'The cited source states a different currency than the extracted value, and this system never applies an exchange rate the source itself does not state.';
  }
  return 'The available document structure was insufficient to confirm this value deterministically. No value was guessed.';
}

// Keyed on the ACTUAL reason strings fact_layer/extract.py's
// _append_rejected() call sites produce (verified against the real backend
// source: "no_subject", "no_measure", "quote_not_found", "unparseable_value",
// "value_mismatch" - the complete set, no others exist). Every one of these
// is a candidate the pipeline extracted and then deliberately refused to
// admit into the trusted store, rather than a system failure being hidden.
const REJECTION_REASON_EXPLANATIONS: Record<string, string> = {
  no_subject: 'The extracted candidate had no subject at all - there is nothing to anchor this claim to, so it was never admitted.',
  no_measure: 'The extracted candidate had no measure at all - a value with no named attribute is not a usable fact.',
  quote_not_found: 'The quote this candidate claims to be grounded in could not be matched back to the source PDF text with sufficient confidence - the fact would have had no verifiable evidence span.',
  unparseable_value: 'The stated value could not be parsed into a number by the same deterministic parser every accepted fact uses - accepting it would have meant guessing what it means.',
  value_mismatch: 'The value disagreed with the sole number the verified source quote actually supports - the candidate was not admitted rather than trusted against its own cited evidence.',
};

export function getRejectionReasonExplanation(reason: string): string {
  if (!reason) return 'The candidate did not meet this system’s evidence-grounding requirements and was not admitted to the trusted store.';
  return REJECTION_REASON_EXPLANATIONS[reason] ||
    'The candidate did not meet this system’s evidence-grounding requirements and was not admitted to the trusted store.';
}
