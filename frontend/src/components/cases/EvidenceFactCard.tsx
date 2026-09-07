import React from 'react';
import { FactFull, FactSummary } from '@/types';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { FileSearch, CheckCircle2, AlertCircle, HelpCircle } from 'lucide-react';
import { formatIssuer, cn } from '@/lib/utils';
import { deriveSpanVerification, deriveValueVerification, deriveContextVerification } from '@/lib/verification';
import { useTheme } from '@/context/ThemeContext';

interface EvidenceFactCardProps {
  role: string; // e.g. "Fact A", "Fact B"
  fact: FactFull;
  accent: 'sky' | 'indigo' | 'rose' | 'emerald';
  onOpenEvidence: (fact: FactSummary) => void;
}

const ACCENT_TEXT: Record<string, string> = {
  sky: 'text-sky-500',
  indigo: 'text-indigo-500',
  rose: 'text-rose-500',
  emerald: 'text-emerald-500',
};

const ACCENT_BTN: Record<string, string> = {
  sky: 'bg-sky-500/10 hover:bg-sky-500/20 text-sky-600 dark:text-sky-400 border-sky-500/20',
  indigo: 'bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 border-indigo-500/20',
  rose: 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border-rose-500/20',
  emerald: 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border-emerald-500/20',
};

const VerificationChip: React.FC<{ label: string; state: 'confirmed' | 'failed' | 'unknown'; detail?: string }> = ({
  label,
  state,
  detail,
}) => {
  const Icon = state === 'confirmed' ? CheckCircle2 : state === 'failed' ? AlertCircle : HelpCircle;
  const cls =
    state === 'confirmed'
      ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
      : state === 'failed'
        ? 'text-amber-700 dark:text-amber-400 bg-amber-500/10 border-amber-500/20'
        : 'text-slate-500 dark:text-slate-400 bg-slate-500/10 border-slate-500/20';
  return (
    <span
      className={cn('inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-mono font-medium', cls)}
      title={detail}
    >
      <Icon className="w-2.5 h-2.5" />
      {label}
    </span>
  );
};

export const EvidenceFactCard: React.FC<EvidenceFactCardProps> = ({ role, fact, accent, onOpenEvidence }) => {
  const { isDark } = useTheme();
  const primaryEvidence = fact.evidence && fact.evidence.length > 0 ? fact.evidence[0] : null;

  const span = deriveSpanVerification(primaryEvidence, false);
  const value = deriveValueVerification(fact);
  const context = deriveContextVerification(fact);

  const periodLabel =
    fact.qualifiers.period?.label ||
    (fact.qualifiers.period?.start || fact.qualifiers.period?.end
      ? `${fact.qualifiers.period?.start || ''} – ${fact.qualifiers.period?.end || ''}`.trim()
      : 'Not stated');

  return (
    <div
      className={cn(
        'p-4 rounded-xl border space-y-3',
        isDark ? 'bg-slate-900/70 border-slate-800' : 'bg-white border-slate-200'
      )}
    >
      <div className={cn('flex items-center justify-between border-b pb-2', isDark ? 'border-slate-800' : 'border-slate-200')}>
        <span className={cn('text-xs font-mono font-bold uppercase tracking-wide', ACCENT_TEXT[accent])}>{role}</span>
        <ConfidencePill confidence={fact.confidence} />
      </div>

      <div className="text-center py-2">
        <div className={cn('text-3xl font-mono font-black', isDark ? 'text-slate-100' : 'text-slate-900')}>
          {fact.value.normalized}
          {fact.value.unit && (
            <span className={cn('text-base font-normal ml-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
              {fact.value.unit}
            </span>
          )}
        </div>
        <div className={cn('text-[11px] font-mono mt-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
          "{fact.value.raw}"
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] font-mono">
        <div>
          <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>SUBJECT</span>
          <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{fact.subject}</span>
        </div>
        <div>
          <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>MEASURE</span>
          <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{fact.measure}</span>
        </div>
        <div>
          <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>PERIOD</span>
          <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{periodLabel}</span>
        </div>
        <div>
          <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>ISSUER</span>
          <span className={cn('truncate block', isDark ? 'text-slate-300' : 'text-slate-700')}>
            {formatIssuer(fact.qualifiers.issuer)}
          </span>
        </div>
        <div className="col-span-2">
          <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>SOURCE</span>
          <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
            Page {fact.page ?? primaryEvidence?.page ?? '—'}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 pt-1">
        <VerificationChip label={span.state === 'confirmed' ? 'Span' : span.state === 'failed' ? 'Span unverified' : 'Span unknown'} state={span.state} detail={span.detail} />
        <VerificationChip label={value.state === 'confirmed' ? 'Value' : value.state === 'failed' ? 'Value unverified' : 'Value not evaluated'} state={value.state} detail={value.detail} />
        <VerificationChip label={context.state === 'confirmed' ? 'Context' : 'Context not established'} state={context.state} detail={context.detail} />
      </div>

      <button
        onClick={() => onOpenEvidence(fact)}
        className={cn(
          'w-full py-2 px-3 rounded-lg border text-xs font-mono font-semibold flex items-center justify-center gap-1.5 transition-colors',
          ACCENT_BTN[accent]
        )}
      >
        <FileSearch className="w-3.5 h-3.5" />
        View Evidence
      </button>
    </div>
  );
};
