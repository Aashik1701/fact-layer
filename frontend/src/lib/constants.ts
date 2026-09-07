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

export const REASON_CODE_CAVEATS: Record<string, string> = {
  PERIOD_MISMATCH: 'Reported values apply to different timeframes or fiscal years (e.g. FY24 vs FY25).',
  MODALITY_MISMATCH: 'One source reports a realized POINT value while the other reports a PROJECTION or ESTIMATE.',
  SCOPE_MISMATCH: 'Geographical or institutional boundaries differ (e.g., General Government vs Central Government).',
  METHODOLOGY_MISMATCH: 'Underlying calculation methodologies or statistical baselines differ (e.g. Constant vs Current prices).',
  SUBGROUP_MISMATCH: 'Data isolates a specific sub-population rather than the headline aggregate.',
  UNGROUNDED_QUOTE: 'Extracted quote could not be strictly verified against raw PDF characters.',
  LOW_OCR_CONFIDENCE: 'Source PDF text layer quality was below strict verification thresholds.',
};

export function getCaveatExplanation(reasonCode: string): string {
  return REASON_CODE_CAVEATS[reasonCode] || 'Contextual qualifier difference detected by the comparability gate.';
}
