import React from 'react';
import { RelationSummary } from '@/types';
import { GateVerdictBadge } from '@/components/relations/GateVerdictBadge';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { ArrowRight, ChevronRight } from 'lucide-react';
import { getCaveatExplanation } from '@/lib/constants';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface LatestFindingsProps {
  relations: RelationSummary[];
  onSelectRelation: (id: string) => void;
  onNavigateToRelations: () => void;
}

export const LatestFindings: React.FC<LatestFindingsProps> = ({
  relations,
  onSelectRelation,
  onNavigateToRelations,
}) => {
  const { isDark } = useTheme();

  const priorityOrder: Record<string, number> = {
    APPARENT_CONFLICT: 1, CONTRADICTS: 2, CORROBORATES: 3,
    SUPERSEDES: 4, AGGREGATES_INTO: 5,
  };

  const sorted = [...relations].sort((a, b) => {
    const normA = (a.relation || '').toUpperCase();
    const normB = (b.relation || '').toUpperCase();
    const pA = priorityOrder[normA] ?? 99;
    const pB = priorityOrder[normB] ?? 99;
    if (pA !== pB) return pA - pB;
    return b.confidence - a.confidence;
  });

  const displayList = sorted.slice(0, 6);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h3
            className={cn(
              'text-sm font-bold uppercase tracking-wider',
              isDark ? 'text-slate-200' : 'text-slate-700'
            )}
          >
            Prioritized Cross-Document Findings
          </h3>
          <p className={cn('text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Adjudicated relations ordered by analytical urgency
          </p>
        </div>
        <button
          onClick={onNavigateToRelations}
          className={cn(
            'text-xs font-mono flex items-center gap-1 transition-colors',
            isDark ? 'text-sky-400 hover:text-sky-300' : 'text-sky-600 hover:text-sky-500'
          )}
        >
          View all {relations.length} relations →
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {displayList.map((rel) => (
          <div
            key={rel.relation_id}
            onClick={() => onSelectRelation(rel.relation_id)}
            className={cn(
              'p-4 rounded-xl border cursor-pointer space-y-2.5 group transition-all',
              isDark
                ? 'bg-slate-900/70 border-slate-800 hover:border-sky-500/30 hover:bg-slate-900'
                : 'bg-white border-slate-200 hover:border-sky-300 hover:shadow-md shadow-sm'
            )}
          >
            <div className="flex items-center justify-between">
              <GateVerdictBadge verdict={rel.relation} size="sm" />
              <div className="flex items-center gap-1.5">
                <ConfidencePill confidence={rel.confidence} />
                <ChevronRight
                  className={cn(
                    'w-4 h-4 transition-all group-hover:translate-x-0.5',
                    isDark ? 'text-slate-500 group-hover:text-sky-400' : 'text-slate-300 group-hover:text-sky-500'
                  )}
                />
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs font-mono truncate">
              <span
                className="text-sky-500 font-semibold truncate"
                title={rel.source_summary || ''}
              >
                {rel.source_summary}
              </span>
              <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              <span
                className="text-indigo-500 font-semibold truncate"
                title={rel.target_summary || ''}
              >
                {rel.target_summary}
              </span>
            </div>

            {rel.reason_code && (
              <p
                className={cn(
                  'text-[11px] line-clamp-1',
                  isDark ? 'text-slate-400' : 'text-slate-500'
                )}
              >
                <span className="text-amber-500 font-mono">[{rel.reason_code}]</span>{' '}
                {getCaveatExplanation(rel.reason_code)}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
