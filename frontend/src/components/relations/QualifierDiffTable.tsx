import React from 'react';
import { FactFull, FactSummary } from '@/types';
import { Check, AlertCircle } from 'lucide-react';
import { cn, formatIssuer } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface QualifierDiffTableProps {
  factA: FactFull | FactSummary;
  factB: FactFull | FactSummary;
  qualifierDiff?: Record<string, any>;
  className?: string;
}

export const QualifierDiffTable: React.FC<QualifierDiffTableProps> = ({
  factA,
  factB,
  qualifierDiff = {},
  className,
}) => {
  const { isDark } = useTheme();

  const periodA =
    factA.qualifiers?.period?.label ||
    `${factA.qualifiers?.period?.start || ''} - ${factA.qualifiers?.period?.end || ''}`.trim() ||
    'N/A';
  const periodB =
    factB.qualifiers?.period?.label ||
    `${factB.qualifiers?.period?.start || ''} - ${factB.qualifiers?.period?.end || ''}`.trim() ||
    'N/A';

  const scopeA = factA.qualifiers?.scope || 'default';
  const scopeB = factB.qualifiers?.scope || 'default';

  const modalityA = factA.modality || 'ASSERTED';
  const modalityB = factB.modality || 'ASSERTED';

  const issuerA = formatIssuer(factA.qualifiers?.issuer);
  const issuerB = formatIssuer(factB.qualifiers?.issuer);

  const methodA = factA.qualifiers?.basis || 'standard';
  const methodB = factB.qualifiers?.basis || 'standard';

  const rows = [
    {
      dimension: 'Reporting Period',
      sourceA: periodA,
      sourceB: periodB,
      isDiff: periodA !== periodB || 'period' in qualifierDiff,
      note: periodA !== periodB ? 'Mismatch: comparing different fiscal periods' : 'Identical period',
    },
    {
      dimension: 'Modality',
      sourceA: modalityA,
      sourceB: modalityB,
      isDiff: modalityA !== modalityB || 'modality' in qualifierDiff,
      note:
        modalityA !== modalityB
          ? 'Mismatch: one is realized point vs projection/estimate'
          : 'Compatible modalities',
    },
    {
      dimension: 'Institutional Scope',
      sourceA: scopeA,
      sourceB: scopeB,
      isDiff: scopeA !== scopeB || 'scope' in qualifierDiff,
      note: scopeA !== scopeB ? 'Scope boundary divergence' : 'Aligned boundaries',
    },
    {
      dimension: 'Reporting Issuer',
      sourceA: issuerA,
      sourceB: issuerB,
      isDiff: issuerA !== issuerB,
      note: issuerA !== issuerB ? 'Cross-institution comparison' : 'Same issuer',
    },
    {
      dimension: 'Measurement Basis',
      sourceA: methodA,
      sourceB: methodB,
      isDiff: methodA !== methodB || 'basis' in qualifierDiff,
      note: methodA !== methodB ? 'Different accounting or calculation basis' : 'Uniform basis',
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
                  {row.isDiff ? (
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
