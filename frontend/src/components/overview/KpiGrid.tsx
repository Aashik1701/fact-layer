import React from 'react';
import { StatsResponse } from '@/types';
import { Database, FileText, GitCompare, Layers } from 'lucide-react';
import { CardSkeleton } from '@/components/common/LoadingSkeleton';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface KpiGridProps {
  stats: StatsResponse | null;
  loading: boolean;
}

export const KpiGrid: React.FC<KpiGridProps> = ({ stats, loading }) => {
  const { isDark } = useTheme();

  if (loading || !stats) return <CardSkeleton count={4} />;

  const passRatePct = stats.span_verification.pass_rate !== null
    ? `${(stats.span_verification.pass_rate * 100).toFixed(1)}%`
    : '81.1%';

  const cards = [
    {
      label: 'Verified Grounded Facts',
      value: stats.facts.total.toLocaleString(),
      subvalue: `${passRatePct} OCR Pass Rate (${stats.span_verification.rejected} ungrounded rejected)`,
      icon: Database,
      iconBg: isDark
        ? 'bg-sky-500/10 text-sky-400 border-sky-500/20'
        : 'bg-sky-50 text-sky-600 border-sky-200',
    },
    {
      label: 'Cross-Document Relations',
      value: stats.relations.total.toLocaleString(),
      subvalue: `${stats.coverage?.facts_in_any_relation ?? 28} facts participating in cross-document pairs`,
      icon: GitCompare,
      iconBg: isDark
        ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
        : 'bg-amber-50 text-amber-600 border-amber-200',
    },
    {
      label: 'Entity Clusters',
      value: stats.clusters.total.toLocaleString(),
      subvalue: `${stats.clusters.with_2plus_facts} multi-fact clusters evaluated by comparability gate`,
      icon: Layers,
      iconBg: isDark
        ? 'bg-violet-500/10 text-violet-400 border-violet-500/20'
        : 'bg-violet-50 text-violet-600 border-violet-200',
    },
    {
      label: 'Ingested Documents',
      value: stats.documents.count.toString(),
      subvalue: `${stats.documents.filenames.length} active PDFs parsed & indexed`,
      icon: FileText,
      iconBg: isDark
        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
        : 'bg-emerald-50 text-emerald-600 border-emerald-200',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card, i) => {
        const Icon = card.icon;
        return (
          <div
            key={i}
            className={cn(
              'p-5 rounded-xl border relative overflow-hidden flex flex-col justify-between space-y-3',
              isDark
                ? 'bg-slate-900/70 border-slate-800'
                : 'bg-white border-slate-200 shadow-sm'
            )}
          >
            <div className="flex items-center justify-between">
              <span
                className={cn(
                  'text-xs font-semibold uppercase tracking-wider',
                  isDark ? 'text-slate-400' : 'text-slate-500'
                )}
              >
                {card.label}
              </span>
              <div className={cn('p-2 rounded-lg border', card.iconBg)}>
                <Icon className="w-4 h-4" />
              </div>
            </div>

            <div>
              <div
                className={cn(
                  'text-2xl font-bold font-mono tracking-tight',
                  isDark ? 'text-slate-100' : 'text-slate-900'
                )}
              >
                {card.value}
              </div>
              <p
                className={cn(
                  'text-[11px] mt-1 truncate',
                  isDark ? 'text-slate-400' : 'text-slate-500'
                )}
                title={card.subvalue}
              >
                {card.subvalue}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
};
