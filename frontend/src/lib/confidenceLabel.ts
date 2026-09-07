import { GateInfo } from '@/types';

// A single honest-uncertainty vocabulary layered on top of real backend
// signals — never a judgment about content, only a deterministic function of
// fields the API already returns (relation.confidence, gate.verdict,
// reason_code). Priority order matters: a gate that never found the two
// facts comparable (INCOMPARABLE) or a comparison whose basis is itself
// unverified (AMBIGUOUS) must win over a merely-high confidence number,
// otherwise a high numeric score would silently paper over a real caveat.
export type ConfidenceLabel =
  | 'CONFIDENT'
  | 'SUPPORTED'
  | 'PARTIALLY VERIFIED'
  | 'INCOMPARABLE'
  | 'AMBIGUOUS'
  | 'REJECTED';

export interface ConfidenceLabelInfo {
  label: ConfidenceLabel;
  detail: string;
}

export function deriveRelationConfidenceLabel(
  relation: { confidence: number; reason_code: string },
  gate?: GateInfo
): ConfidenceLabelInfo {
  if (gate && gate.verdict !== 'comparable') {
    return {
      label: 'INCOMPARABLE',
      detail: `The comparability gate never validated a like-for-like comparison here (${gate.verdict.replace(/_/g, ' ')}).`,
    };
  }
  if (relation.reason_code && relation.reason_code.endsWith('_period_unverified')) {
    return {
      label: 'AMBIGUOUS',
      detail: 'At least one side states no reporting period, so the comparison basis itself is unverified.',
    };
  }
  if (relation.confidence >= 0.85) {
    return { label: 'CONFIDENT', detail: 'High confidence, on a fully validated like-for-like basis.' };
  }
  if (relation.confidence >= 0.65) {
    return { label: 'SUPPORTED', detail: 'A validated comparison with reasonable but not maximal confidence.' };
  }
  return { label: 'PARTIALLY VERIFIED', detail: 'A validated comparison, but confidence is limited.' };
}
