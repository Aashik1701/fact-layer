import React, { useState, useEffect } from 'react';
import { fetchFacts } from '@/lib/api';
import { FactSummary } from '@/types';
import { FactsTable } from './FactsTable';
import { FactDetailDrawer } from './FactDetailDrawer';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { RelationInspectorModal } from '@/components/relations/RelationInspectorModal';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { EmptyState } from '@/components/common/EmptyState';
import { Database, Search, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

export const FactsPage: React.FC = () => {
  const { isDark } = useTheme();
  const [facts, setFacts] = useState<FactSummary[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [page, setPage] = useState<number>(0);
  const pageSize = 25;
  const [searchSubject, setSearchSubject] = useState<string>('');
  const [searchMeasure, setSearchMeasure] = useState<string>('');
  const [minConfidence, setMinConfidence] = useState<number>(0);

  const [selectedFact, setSelectedFact] = useState<FactSummary | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactSummary | null>(null);
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);

  const loadFacts = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFacts({
        subject: searchSubject.trim() || undefined,
        measure: searchMeasure.trim() || undefined,
        min_confidence: minConfidence > 0 ? minConfidence : undefined,
        limit: pageSize,
        offset: page * pageSize,
      });
      setFacts(res.facts);
      setTotal(res.total);
    } catch (err) {
      console.error('Failed to load facts:', err);
      setError('Unable to retrieve facts from knowledge layer.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadFacts(); }, [page, searchSubject, searchMeasure, minConfidence]);

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <Database className="w-5 h-5 text-sky-500" />
            Facts Explorer
          </h2>
          <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Grounded numerical and categorical facts extracted and normalized across documents ({total} total)
          </p>
        </div>

        <button
          onClick={loadFacts}
          className={cn(
            'self-start sm:self-auto px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border',
            isDark
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
          )}
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {/* Filter Controls */}
      <div
        className={cn(
          'p-4 rounded-xl border grid grid-cols-1 sm:grid-cols-2 md:grid-cols-12 gap-3',
          isDark ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
        )}
      >
        {/* Subject Filter */}
        <div className="md:col-span-4 relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Filter by Subject (e.g. india)..."
            value={searchSubject}
            onChange={(e) => { setSearchSubject(e.target.value); setPage(0); }}
            className={cn(
              'w-full pl-9 pr-3 py-2 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60',
              isDark
                ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
                : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
            )}
          />
        </div>

        {/* Measure Filter */}
        <div className="md:col-span-4 relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Filter by Measure (e.g. gdp_growth)..."
            value={searchMeasure}
            onChange={(e) => { setSearchMeasure(e.target.value); setPage(0); }}
            className={cn(
              'w-full pl-9 pr-3 py-2 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60',
              isDark
                ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
                : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
            )}
          />
        </div>

        {/* Confidence Slider */}
        <div
          className={cn(
            'md:col-span-4 flex items-center gap-3 px-3 py-2 rounded-lg border text-xs',
            isDark ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-200'
          )}
        >
          <span className={cn('text-[11px] whitespace-nowrap', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Min Confidence:
          </span>
          <input
            type="range" min="0" max="1" step="0.05"
            value={minConfidence}
            onChange={(e) => { setMinConfidence(parseFloat(e.target.value)); setPage(0); }}
            className="w-full accent-sky-500 cursor-pointer h-1.5 rounded"
          />
          <span className={cn('font-mono min-w-[2.5rem] text-right text-[11px]', isDark ? 'text-slate-200' : 'text-slate-700')}>
            {Math.round(minConfidence * 100)}%
          </span>
        </div>
      </div>

      {/* Table or State */}
      {loading ? (
        <LoadingSkeleton rows={8} />
      ) : error ? (
        <div className="p-8 text-center bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-sm">
          {error}
        </div>
      ) : facts.length === 0 ? (
        <EmptyState
          icon={Database}
          title="No Facts Found"
          description="No facts matched the given filters. Try clearing the subject or measure filters."
          action={{
            label: 'Clear Filters',
            onClick: () => { setSearchSubject(''); setSearchMeasure(''); setMinConfidence(0); setPage(0); },
          }}
        />
      ) : (
        <div className="space-y-4">
          <FactsTable
            facts={facts}
            onSelectFact={(f) => setSelectedFact(f)}
            onOpenEvidence={(f) => setEvidenceFact(f)}
          />

          {/* Pagination */}
          <div
            className={cn(
              'flex items-center justify-between px-2 text-xs font-mono',
              isDark ? 'text-slate-400' : 'text-slate-500'
            )}
          >
            <span>
              Showing {page * pageSize + 1}–{Math.min((page + 1) * pageSize, total)} of {total} facts
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className={cn(
                  'p-1.5 rounded border disabled:opacity-40 disabled:cursor-not-allowed transition-colors',
                  isDark
                    ? 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700'
                    : 'bg-slate-100 text-slate-600 border-slate-200 hover:bg-slate-200'
                )}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span>Page {page + 1} of {Math.max(1, totalPages)}</span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className={cn(
                  'p-1.5 rounded border disabled:opacity-40 disabled:cursor-not-allowed transition-colors',
                  isDark
                    ? 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700'
                    : 'bg-slate-100 text-slate-600 border-slate-200 hover:bg-slate-200'
                )}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      <FactDetailDrawer
        isOpen={!!selectedFact}
        onClose={() => setSelectedFact(null)}
        fact={selectedFact}
        onOpenEvidence={(f) => setEvidenceFact(f)}
        onOpenRelation={(relationId) => { setSelectedFact(null); setSelectedRelationId(relationId); }}
      />

      <EvidenceModal
        isOpen={!!evidenceFact}
        onClose={() => setEvidenceFact(null)}
        fact={evidenceFact}
      />

      <RelationInspectorModal
        isOpen={!!selectedRelationId}
        onClose={() => setSelectedRelationId(null)}
        relationId={selectedRelationId}
        onOpenEvidence={(f) => setEvidenceFact(f)}
      />
    </div>
  );
};
