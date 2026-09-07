import React from 'react';
import { RelationSummary } from '@/types';
import { GateVerdictBadge } from './GateVerdictBadge';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { ArrowRight, ChevronRight, Cpu, Search } from 'lucide-react';
import { getCaveatExplanation } from '@/lib/constants';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface RelationCardProps {
  relation: RelationSummary;
  onClick: () => void;
}

export const RelationCard: React.FC<RelationCardProps> = ({ relation, onClick }) => {
  const { isDark } = useTheme();

  return (
    <div
      onClick={onClick}
      className={cn(
        'p-4 rounded-xl border cursor-pointer space-y-3 group transition-all',
        isDark
          ? 'bg-slate-900/70 border-slate-800 hover:border-sky-500/30 hover:bg-slate-900 hover:shadow-lg hover:shadow-sky-500/5'
          : 'bg-white border-slate-200 hover:border-sky-300 hover:shadow-md shadow-sm'
      )}
    >
      <div className="flex items-center justify-between">
        <GateVerdictBadge verdict={relation.relation} size="sm" />
        <div className="flex items-center gap-2">
          <ConfidencePill confidence={relation.confidence} />
          <ChevronRight
            className={cn(
              'w-4 h-4 transition-all group-hover:translate-x-0.5',
              isDark
                ? 'text-slate-500 group-hover:text-sky-400'
                : 'text-slate-300 group-hover:text-sky-500'
            )}
          />
        </div>
      </div>

      <div className="space-y-1">
        <div
          className={cn(
            'flex items-center gap-2 text-xs font-mono truncate',
            isDark ? 'text-slate-200' : 'text-slate-700'
          )}
        >
          <span className="text-sky-500 truncate font-semibold" title={relation.source_summary || ''}>
            {relation.source_summary || relation.source_fact_id.slice(0, 8)}
          </span>
          <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <span className="text-indigo-500 truncate font-semibold" title={relation.target_summary || ''}>
            {relation.target_summary || relation.target_fact_id.slice(0, 8)}
          </span>
        </div>

        {relation.reason_code && (
          <p className={cn('text-[11px] line-clamp-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            <span className="text-amber-500 font-mono">[{relation.reason_code}]</span>{' '}
            {getCaveatExplanation(relation.reason_code)}
          </p>
        )}
      </div>

      <div
        className={cn(
          'flex items-center justify-between pt-2 border-t text-[10px] font-mono',
          isDark
            ? 'border-slate-800/80 text-slate-500'
            : 'border-slate-100 text-slate-400'
        )}
      >
        <span className="flex items-center gap-1">
          <Cpu className={cn('w-3 h-3', isDark ? 'text-slate-600' : 'text-slate-300')} />
          Decided by:{' '}
          <span className={cn('uppercase', isDark ? 'text-slate-400' : 'text-slate-600')}>
            {relation.decided_by}
          </span>
        </span>
        <span>ID: {relation.relation_id.slice(0, 8)}</span>
      </div>

      <div
        className={cn(
          'flex items-center justify-center gap-1.5 pt-1 text-[11px] font-mono font-semibold transition-colors',
          isDark ? 'text-sky-400 group-hover:text-sky-300' : 'text-sky-600 group-hover:text-sky-500'
        )}
      >
        <Search className="w-3 h-3" />
        Explain This Conclusion
      </div>
    </div>
  );
};
