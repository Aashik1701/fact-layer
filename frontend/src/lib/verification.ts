import { EvidenceItem, FactSummary } from '@/types';
import { getValueVerificationExplanation } from '@/lib/constants';

export type VerificationState = 'confirmed' | 'failed' | 'unknown';

export interface VerificationRowInfo {
  label: string;
  state: VerificationState;
  detail?: string;
}

// Span verification is per-evidence (Evidence.verified, set only by the span
// verifier) - meaningless until the fact's evidence array has loaded, so
// `loading` renders a distinct "unknown, still loading" state rather than a
// false negative.
export function deriveSpanVerification(
  primaryEvidence: EvidenceItem | null | undefined,
  loading: boolean
): VerificationRowInfo {
  if (loading) {
    return { label: 'Span Verified (loading…)', state: 'unknown' };
  }
  if (!primaryEvidence) {
    return { label: 'Span Verification Unknown', state: 'unknown', detail: 'No evidence record was found for this fact.' };
  }
  return primaryEvidence.verified
    ? { label: 'Span Verified', state: 'confirmed' }
    : { label: 'Span Not Verified', state: 'failed' };
}

// value_verification is "" / null / undefined (not "unverified") when the
// deterministic check never ran against this fact - that must render as
// "not evaluated", never as a failure. See fact_layer/models.py's
// Fact.value_verification docstring and value_verify.py's status enum.
export function deriveValueVerification(fact: FactSummary): VerificationRowInfo {
  const vv = fact.value_verification;
  if (vv === 'verified' || vv === 'verified_with_context') {
    return { label: 'Value Verified', state: 'confirmed' };
  }
  if (vv === 'mismatch' || vv === 'unverified') {
    return {
      label: 'Value Not Verified',
      state: 'failed',
      detail: fact.value_verification_reason ? getValueVerificationExplanation(fact.value_verification_reason) : undefined,
    };
  }
  return {
    label: 'Value Verification Not Evaluated',
    state: 'unknown',
    detail: 'This fact was persisted before deterministic value verification existed, or the check has not run.',
  };
}

// Context Verified is exactly value_verification === 'verified_with_context'
// - never implied by the presence of row/column labels alone, and there is
// no "failed" outcome for this check, only "confirmed" or "not established".
export function deriveContextVerification(fact: FactSummary): VerificationRowInfo {
  const vv = fact.value_verification;
  if (vv === 'verified_with_context') {
    return {
      label: 'Context Verified',
      state: 'confirmed',
      detail: 'A table cell confidently confirms the row, column and unit this value belongs to.',
    };
  }
  return {
    label: 'Context Not Established',
    state: 'unknown',
    detail:
      vv === 'verified'
        ? 'Value confirmed, but no single table cell could be confidently attributed - no row/column context to show.'
        : 'Not applicable until the value itself is verified.',
  };
}
