import React from 'react';
import { FactSummary } from '@/types';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { FileSearch, Eye, Clock } from 'lucide-react';
import { formatIssuer } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface FactsTableProps {
  facts: FactSummary[];
  onSelectFact: (fact: FactSummary) => void;
  onOpenEvidence: (fact: FactSummary) => void;
}

export const FactsTable: React.FC<FactsTableProps> = ({
  facts,
  onSelectFact,
  onOpenEvidence,
}) => {
  const { isDark } = useTheme();

  return (
    <div
      className={cn(
        'overflow-x-auto rounded-xl border',
        isDark ? 'border-slate-800 bg-slate-900/60' : 'border-slate-200 bg-white shadow-sm'
      )}
    >
      <table className="w-full text-xs text-left">
        <thead
          className={cn(
            'font-mono text-[11px] border-b uppercase tracking-wider',
            isDark
              ? 'bg-slate-900 text-slate-400 border-slate-800'
              : 'bg-slate-50 text-slate-500 border-slate-200'
          )}
        >
          <tr>
            <th className="py-3 px-4">Subject :: Measure</th>
            <th className="py-3 px-4">Canonical Value</th>
            <th className="py-3 px-4">Period</th>
            <th className="py-3 px-4">Modality</th>
            <th className="py-3 px-4">Issuer</th>
            <th className="py-3 px-4">Confidence</th>
            <th className="py-3 px-4 text-right">Actions</th>
          </tr>
        </thead>
        <tbody
          className={cn(
            'font-sans',
            isDark ? 'divide-y divide-slate-800/60' : 'divide-y divide-slate-100'
          )}
        >
          {facts.map((fact) => {
            const periodLabel =
              fact.qualifiers.period?.label ||
              `${fact.qualifiers.period?.start || ''} - ${fact.qualifiers.period?.end || ''}`.trim() ||
              '—';

            return (
              <tr
                key={fact.fact_id}
                className={cn(
                  'transition-colors group cursor-pointer',
                  isDark ? 'hover:bg-slate-800/40' : 'hover:bg-sky-50/40'
                )}
                onClick={() => onSelectFact(fact)}
              >
                {/* Subject & Measure */}
                <td className="py-3 px-4">
                  <div className="flex flex-col">
                    <span className="font-mono text-sky-500 font-medium">
                      {fact.subject}::{fact.measure}
                    </span>
                    <span
                      className={cn(
                        'text-[10px] font-mono truncate max-w-[200px]',
                        isDark ? 'text-slate-500' : 'text-slate-400'
                      )}
                    >
                      ID: {fact.fact_id.slice(0, 10)}
                    </span>
                  </div>
                </td>

                {/* Canonical Value */}
                <td className="py-3 px-4 font-mono">
                  <div className="flex flex-col">
                    <span className="text-emerald-500 font-bold">
                      {fact.value.normalized} {fact.value.unit || ''}
                    </span>
                    <span
                      className={cn(
                        'text-[10px] truncate max-w-[140px]',
                        isDark ? 'text-slate-400' : 'text-slate-500'
                      )}
                      title={fact.value.raw}
                    >
                      "{fact.value.raw}"
                    </span>
                  </div>
                </td>

                {/* Period */}
                <td className={cn('py-3 px-4 font-mono', isDark ? 'text-slate-300' : 'text-slate-600')}>
                  <div className="flex items-center gap-1">
                    <Clock className={cn('w-3 h-3', isDark ? 'text-slate-500' : 'text-slate-400')} />
                    <span className="truncate max-w-[120px]">{periodLabel}</span>
                  </div>
                </td>

                {/* Modality */}
                <td className="py-3 px-4">
                  <span
                    className={cn(
                      'px-2 py-0.5 rounded text-[10px] font-mono border',
                      isDark
                        ? 'bg-slate-800 text-slate-300 border-slate-700'
                        : 'bg-slate-100 text-slate-600 border-slate-200'
                    )}
                  >
                    {fact.modality}
                  </span>
                </td>

                {/* Issuer */}
                <td className={cn('py-3 px-4', isDark ? 'text-slate-300' : 'text-slate-600')}>
                  <span className="truncate block max-w-[140px]" title={fact.qualifiers.issuer || ''}>
                    {formatIssuer(fact.qualifiers.issuer)}
                  </span>
                </td>

                {/* Confidence */}
                <td className="py-3 px-4">
                  <ConfidencePill confidence={fact.confidence} />
                </td>

                {/* Actions */}
                <td className="py-3 px-4 text-right">
                  <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => onSelectFact(fact)}
                      className={cn(
                        'p-1.5 rounded transition-colors',
                        isDark
                          ? 'hover:bg-slate-700 text-slate-400 hover:text-slate-200'
                          : 'hover:bg-slate-100 text-slate-400 hover:text-slate-700'
                      )}
                      title="Inspect Fact Details"
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => onOpenEvidence(fact)}
                      className="p-1.5 rounded hover:bg-sky-500/20 text-sky-500 transition-colors"
                      title="View PDF Grounding"
                    >
                      <FileSearch className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
