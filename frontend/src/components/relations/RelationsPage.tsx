import React, { useState, useEffect } from 'react';
import { fetchRelations } from '@/lib/api';
import { RelationSummary, RelationType, FactFull, FactSummary } from '@/types';
import { RelationCard } from './RelationCard';
import { RelationInspectorModal } from './RelationInspectorModal';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { EmptyState } from '@/components/common/EmptyState';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { RELATION_CONFIG } from '@/lib/constants';
import { Search, GitCompare, RefreshCw } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

export const RelationsPage: React.FC = () => {
  const { isDark } = useTheme();
  const [relations, setRelations] = useState<RelationSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedType, setSelectedType] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [minConfidence, setMinConfidence] = useState<number>(0);

  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactFull | FactSummary | null>(null);

  const loadRelations = async () => {
    setLoading(true);
    setError(null);
    try {
      const typeParam = selectedType === 'ALL' ? undefined : selectedType;
      const res = await fetchRelations({
        type: typeParam,
        min_confidence: minConfidence > 0 ? minConfidence : undefined,
      });
      setRelations(res.relations);
    } catch (err) {
      console.error('Failed to load relations:', err);
      setError('Unable to load relations from knowledge base.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadRelations(); }, [selectedType, minConfidence]);

  const filteredRelations = relations.filter((r) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      (r.source_summary && r.source_summary.toLowerCase().includes(query)) ||
      (r.target_summary && r.target_summary.toLowerCase().includes(query)) ||
      (r.reason_code && r.reason_code.toLowerCase().includes(query)) ||
      r.relation_id.toLowerCase().includes(query)
    );
  });

  const relationTypes = ['ALL', 'APPARENT_CONFLICT', 'CONTRADICTS', 'CORROBORATES', 'SUPERSEDES', 'AGGREGATES_INTO'];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <GitCompare className="w-5 h-5 text-sky-500" />
            Cross-Fact Relations
          </h2>
          <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Pairs evaluated across documents through the deterministic comparability gate
          </p>
        </div>

        <button
          onClick={loadRelations}
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

      {/* Filter Toolbar */}
      <div
        className={cn(
          'p-4 rounded-xl border space-y-4',
          isDark ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
        )}
      >
        {/* Type Filter Chips */}
        <div className="flex flex-wrap items-center gap-2">
          {relationTypes.map((type) => {
            const isAll = type === 'ALL';
            const isSelected = selectedType === type;
            const config = !isAll ? RELATION_CONFIG[type as RelationType] : null;

            return (
              <button
                key={type}
                onClick={() => setSelectedType(type)}
                className={cn(
                  'px-3 py-1 rounded-lg text-xs font-mono transition-all border',
                  isSelected
                    ? isAll
                      ? 'bg-sky-500 text-white font-bold border-sky-400 shadow-md shadow-sky-500/20'
                      : `${config?.bgBadge} ${config?.textBadge} ${config?.borderBadge} font-bold ring-1 ring-offset-1 ${isDark ? 'ring-offset-slate-900' : 'ring-offset-slate-50'}`
                    : isDark
                    ? 'bg-slate-800/60 text-slate-400 border-slate-700/60 hover:bg-slate-800 hover:text-slate-200'
                    : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-100 hover:text-slate-700'
                )}
              >
                {type.replace('_', ' ')}
              </button>
            );
          })}
        </div>

        {/* Search & Confidence Slider */}
        <div
          className={cn(
            'grid grid-cols-1 md:grid-cols-12 gap-3 pt-3 border-t',
            isDark ? 'border-slate-800/60' : 'border-slate-200'
          )}
        >
          <div className="md:col-span-8 relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by subject, measure, trigger code, or relation ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className={cn(
                'w-full pl-9 pr-4 py-2 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60',
                isDark
                  ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
                  : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
              )}
            />
          </div>

          <div
            className={cn(
              'md:col-span-4 flex items-center gap-3 px-3 py-1.5 rounded-lg border text-xs',
              isDark
                ? 'bg-slate-950 border-slate-800'
                : 'bg-white border-slate-200'
            )}
          >
            <span className={cn('text-[11px] whitespace-nowrap', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Min Confidence:
            </span>
            <input
              type="range"
              min="0" max="1" step="0.05"
              value={minConfidence}
              onChange={(e) => setMinConfidence(parseFloat(e.target.value))}
              className="w-full accent-sky-500 cursor-pointer h-1.5 rounded"
            />
            <span className={cn('font-mono min-w-[2.5rem] text-right text-[11px]', isDark ? 'text-slate-200' : 'text-slate-700')}>
              {Math.round(minConfidence * 100)}%
            </span>
          </div>
        </div>
      </div>

      {/* Relations Grid */}
      {loading ? (
        <LoadingSkeleton rows={4} />
      ) : error ? (
        <div className="p-8 text-center bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-sm">
          {error}
        </div>
      ) : filteredRelations.length === 0 ? (
        <EmptyState
          icon={GitCompare}
          title="No Relations Found"
          description="No cross-document relations matched the current filter criteria."
          action={{
            label: 'Reset Filters',
            onClick: () => { setSelectedType('ALL'); setSearchQuery(''); setMinConfidence(0); },
          }}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredRelations.map((rel) => (
            <RelationCard
              key={rel.relation_id}
              relation={rel}
              onClick={() => setSelectedRelationId(rel.relation_id)}
            />
          ))}
        </div>
      )}

      <RelationInspectorModal
        isOpen={!!selectedRelationId}
        onClose={() => setSelectedRelationId(null)}
        relationId={selectedRelationId}
        onOpenEvidence={(fact) => setEvidenceFact(fact)}
      />

      <EvidenceModal
        isOpen={!!evidenceFact}
        onClose={() => setEvidenceFact(null)}
        fact={evidenceFact}
      />
    </div>
  );
};
