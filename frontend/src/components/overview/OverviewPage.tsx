import React, { useState, useEffect } from 'react';
import { fetchStats, fetchRelations } from '@/lib/api';
import { StatsResponse, RelationSummary, FactFull, FactSummary } from '@/types';
import { KpiGrid } from './KpiGrid';
import { LatestFindings } from './LatestFindings';
import { ArchitecturePipeline } from './ArchitecturePipeline';
import { RelationInspectorModal } from '@/components/relations/RelationInspectorModal';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { Sparkles, ArrowRight, RefreshCw, AlertTriangle } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface OverviewPageProps {
  onNavigateToCases: () => void;
  onNavigateToRelations: () => void;
  onNavigateToFacts: () => void;
}

export const OverviewPage: React.FC<OverviewPageProps> = ({
  onNavigateToCases,
  onNavigateToRelations,
}) => {
  const { isDark } = useTheme();
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [relations, setRelations] = useState<RelationSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactFull | FactSummary | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, r] = await Promise.all([fetchStats(), fetchRelations({})]);
      setStats(s);
      setRelations(r.relations);
    } catch (err) {
      console.error('Failed to load overview data:', err);
      setError('Unable to reach the knowledge layer. The backend may be offline or still starting.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  return (
    <div className="space-y-8">
      {/* Hero Banner */}
      <div
        className={cn(
          'p-6 rounded-2xl border shadow-xl relative overflow-hidden flex flex-col sm:flex-row sm:items-center justify-between gap-4',
          isDark
            ? 'bg-gradient-to-r from-sky-950/40 via-slate-900 to-indigo-950/40 border-slate-800'
            : 'bg-gradient-to-r from-sky-50 via-white to-indigo-50 border-slate-200'
        )}
      >
        <div className="space-y-2 max-w-2xl">
          <div
            className={cn(
              'inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-mono font-medium',
              isDark
                ? 'bg-sky-500/10 border-sky-500/20 text-sky-400'
                : 'bg-sky-50 border-sky-200 text-sky-700'
            )}
          >
            <Sparkles className="w-3.5 h-3.5" />
            Core Foundational Thesis
          </div>
          <h1
            className={cn(
              'text-2xl font-bold tracking-tight font-mono',
              isDark ? 'text-slate-100' : 'text-slate-900'
            )}
          >
            COMPARABILITY BEFORE COMPARISON
          </h1>
          <p className={cn('text-xs leading-relaxed', isDark ? 'text-slate-300' : 'text-slate-600')}>
            Standard RAG systems compare numbers blindly. The Fact Knowledge Layer validates time
            periods, scopes, modalities, and methodology across PDF sources before cross-document reasoning.
          </p>
        </div>

        <div className="flex flex-wrap sm:flex-col gap-2 shrink-0">
          <button
            onClick={onNavigateToCases}
            className="px-4 py-2.5 rounded-xl bg-sky-500 hover:bg-sky-400 text-white font-semibold font-mono text-xs flex items-center justify-center gap-2 shadow-lg shadow-sky-500/20 transition-all"
          >
            Explore 4 Required Cases
            <ArrowRight className="w-4 h-4" />
          </button>
          <button
            onClick={loadData}
            className={cn(
              'px-4 py-2 rounded-xl font-mono text-xs flex items-center justify-center gap-2 border transition-colors',
              isDark
                ? 'bg-slate-800/80 hover:bg-slate-700 text-slate-300 border-slate-700'
                : 'bg-slate-100 hover:bg-slate-200 text-slate-700 border-slate-200'
            )}
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh Store Metrics
          </button>
        </div>
      </div>

      {error && (
        <div
          className={cn(
            'rounded-xl border px-4 py-3 flex items-center justify-between gap-3',
            isDark
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-300'
              : 'bg-rose-50 border-rose-200 text-rose-700'
          )}
          role="alert"
        >
          <span className="flex items-center gap-2 text-xs">
            <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
            {error}
          </span>
          <button
            onClick={loadData}
            className={cn(
              'shrink-0 px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 border transition-colors',
              isDark
                ? 'bg-slate-900 border-slate-700 text-slate-200 hover:border-rose-500/50'
                : 'bg-white border-slate-200 text-slate-600 hover:border-rose-400'
            )}
          >
            <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
            Retry
          </button>
        </div>
      )}

      <KpiGrid stats={stats} loading={loading} />

      <LatestFindings
        relations={relations}
        onSelectRelation={(id) => setSelectedRelationId(id)}
        onNavigateToRelations={onNavigateToRelations}
      />

      <ArchitecturePipeline />

      <RelationInspectorModal
        isOpen={!!selectedRelationId}
        onClose={() => setSelectedRelationId(null)}
        relationId={selectedRelationId}
        onOpenEvidence={(f) => setEvidenceFact(f)}
      />

      <EvidenceModal
        isOpen={!!evidenceFact}
        onClose={() => setEvidenceFact(null)}
        fact={evidenceFact}
      />
    </div>
  );
};
