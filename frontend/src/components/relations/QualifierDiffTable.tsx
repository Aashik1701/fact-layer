import React from 'react';
import { FactFull, FactSummary, GateInfo } from '@/types';
import { Check, AlertCircle, HelpCircle } from 'lucide-react';
import { cn, formatIssuer } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface QualifierDiffTableProps {
  factA: FactFull | FactSummary;
  factB: FactFull | FactSummary;
  // The backend-computed diff (Qualifiers.diff(), fact_layer/models.py) —
  // whichever key names a dimension is the ONLY signal used to decide
  // "aligned" vs "different" below. Nothing here re-derives comparability
  // from the raw qualifier values; the gate already did that.
  qualifierDiff?: Record<string, [unknown, unknown]>;
  gate?: GateInfo;
  className?: string;
}

// Every dimension shown is either read directly off the backend's
// qualifier_diff dict (presence of the key = the gate found a difference)
// or, for period, off the gate's own period_relation enum — never a
// frontend string comparison of the two raw values.
export const QualifierDiffTable: React.FC<QualifierDiffTableProps> = ({
  factA,
  factB,
  qualifierDiff = {},
  gate,
  className,
}) => {
  const { isDark } = useTheme();

  const periodLabel = (f: FactFull | FactSummary) =>
    f.qualifiers?.period?.label ||
    (f.qualifiers?.period?.start || f.qualifiers?.period?.end
      ? `${f.qualifiers?.period?.start || ''} - ${f.qualifiers?.period?.end || ''}`.trim()
      : 'Not stated');

  const periodRelation = gate?.period_relation;
  const periodDiff = periodRelation
    ? periodRelation !== 'equal'
    : 'period' in qualifierDiff;
  const periodNote: Record<string, string> = {
    equal: 'Identical reporting period',
    subsumes: "Source A's period contains Source B's — expect a component, not equality",
    subsumed_by: "Source B's period contains Source A's — expect a component, not equality",
    overlaps: 'Periods partially overlap — not on a strictly like-for-like basis',
    disjoint: 'Different reporting periods entirely',
    succeeds: 'Point-in-time claims at different dates — the later one updates the earlier',
    precedes: 'Point-in-time claims at different dates — the later one updates the earlier',
    unknown: 'At least one side states no reporting period — unverifiable, not confirmed aligned',
  };

  const rows: Array<{ dimension: string; sourceA: string; sourceB: string; isDiff: boolean; unverifiable?: boolean; note: string }> = [
    {
      dimension: 'Reporting Period',
      sourceA: periodLabel(factA),
      sourceB: periodLabel(factB),
      isDiff: periodDiff,
      unverifiable: periodRelation === 'unknown',
      note: periodRelation ? periodNote[periodRelation] : (periodDiff ? 'Gate found the periods differ' : 'Gate confirms identical period'),
    },
    {
      dimension: 'Institutional Scope',
      sourceA: factA.qualifiers?.scope || 'unknown',
      sourceB: factB.qualifiers?.scope || 'unknown',
      isDiff: 'scope' in qualifierDiff,
      note: 'scope' in qualifierDiff ? 'Reported on different bases — both can be correct for the same period' : 'Aligned reporting boundary',
    },
    {
      dimension: 'Reporting Issuer',
      sourceA: formatIssuer(factA.qualifiers?.issuer),
      sourceB: formatIssuer(factB.qualifiers?.issuer),
      isDiff: gate ? gate.cross_issuer === true : 'issuer' in qualifierDiff,
      note: (gate ? gate.cross_issuer : 'issuer' in qualifierDiff) ? 'Different issuing institutions' : 'Same issuer',
    },
    {
      dimension: 'Measurement Basis',
      sourceA: factA.qualifiers?.basis || 'not stated',
      sourceB: factB.qualifiers?.basis || 'not stated',
      isDiff: 'basis' in qualifierDiff,
      note: 'basis' in qualifierDiff ? 'Different accounting or calculation basis' : 'Uniform basis',
    },
    {
      dimension: 'Canonical Stated Value',
      sourceA: `${factA.value?.normalized} ${factA.value?.unit || ''}`.trim(),
      sourceB: `${factB.value?.normalized} ${factB.value?.unit || ''}`.trim(),
      isDiff: factA.value?.normalized !== factB.value?.normalized,
      note:
        factA.value?.normalized !== factB.value?.normalized
          ? 'Values diverge numerically'
          : 'Identical numerical values',
    },
  ];

  if ('segment' in qualifierDiff || factA.qualifiers?.segment || factB.qualifiers?.segment) {
    rows.splice(3, 0, {
      dimension: 'Business Segment',
      sourceA: factA.qualifiers?.segment || 'not stated',
      sourceB: factB.qualifiers?.segment || 'not stated',
      isDiff: 'segment' in qualifierDiff,
      note: 'segment' in qualifierDiff ? 'Different business or product segments' : 'Same segment',
    });
  }

  return (
    <div
      className={cn(
        'overflow-x-auto rounded-xl border',
        isDark ? 'border-slate-800 bg-slate-950/60' : 'border-slate-200 bg-white',
        className
      )}
    >
      <table className="w-full text-xs text-left">
        <thead
          className={cn(
            'font-mono text-[11px] border-b uppercase tracking-wider',
            isDark ? 'bg-slate-900/80 text-slate-400 border-slate-800' : 'bg-slate-50 text-slate-600 border-slate-200'
          )}
        >
          <tr>
            <th className="py-2.5 px-4">Qualifier Dimension</th>
            <th className="py-2.5 px-4 text-sky-500">Source A ({factA.subject}::{factA.measure})</th>
            <th className="py-2.5 px-4 text-indigo-500">Source B ({factB.subject}::{factB.measure})</th>
            <th className="py-2.5 px-4">Gate Comparison Verdict</th>
          </tr>
        </thead>
        <tbody className={cn('divide-y', isDark ? 'divide-slate-800/60' : 'divide-slate-200')}>
          {rows.map((row, idx) => (
            <tr
              key={idx}
              className={cn(
                'transition-colors',
                row.isDiff
                  ? isDark
                    ? 'bg-amber-500/[0.04] hover:bg-amber-500/[0.08]'
                    : 'bg-amber-50/60 hover:bg-amber-100/40'
                  : isDark
                    ? 'hover:bg-slate-900/40'
                    : 'hover:bg-slate-50'
              )}
            >
              <td className={cn('py-3 px-4 font-medium', isDark ? 'text-slate-300' : 'text-slate-700')}>
                {row.dimension}
              </td>
              <td className={cn('py-3 px-4 font-mono font-medium', isDark ? 'text-slate-200' : 'text-slate-900')}>
                {row.sourceA}
              </td>
              <td className={cn('py-3 px-4 font-mono font-medium', isDark ? 'text-slate-200' : 'text-slate-900')}>
                {row.sourceB}
              </td>
              <td className="py-3 px-4">
                <div className="flex items-center gap-1.5">
                  {row.unverifiable ? (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded border font-medium',
                        isDark
                          ? 'text-slate-400 bg-slate-800/60 border-slate-700'
                          : 'text-slate-600 bg-slate-100 border-slate-300'
                      )}
                    >
                      <HelpCircle className="w-3 h-3" />
                      {row.note}
                    </span>
                  ) : row.isDiff ? (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded border font-medium',
                        isDark
                          ? 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                          : 'text-amber-700 bg-amber-50 border-amber-200'
                      )}
                    >
                      <AlertCircle className="w-3 h-3 text-amber-500" />
                      {row.note}
                    </span>
                  ) : (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded border font-medium',
                        isDark
                          ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
                          : 'text-emerald-700 bg-emerald-50 border-emerald-200'
                      )}
                    >
                      <Check className="w-3 h-3 text-emerald-500" />
                      Aligned
                    </span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
