import React, { useState, useEffect } from 'react';
import { fetchClusters } from '@/lib/api';
import { ClusterInfo } from '@/types';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { EmptyState } from '@/components/common/EmptyState';
import { Layers, Search, RefreshCw } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface ClustersPageProps {
  onSelectCluster?: (cluster: ClusterInfo) => void;
}

export const ClustersPage: React.FC<ClustersPageProps> = ({ onSelectCluster }) => {
  const { isDark } = useTheme();
  const [clusters, setClusters] = useState<ClusterInfo[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [minSize, setMinSize] = useState<number>(1);

  const loadClusters = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchClusters(minSize);
      setClusters(res.clusters);
      setTotal(res.total);
    } catch (err) {
      console.error('Failed to load clusters:', err);
      setError('Unable to load fact clusters.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadClusters(); }, [minSize]);

  const filteredClusters = clusters.filter((c) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return c.cluster_key.toLowerCase().includes(query);
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <Layers className="w-5 h-5 text-sky-500" />
            Fact Clusters Explorer
          </h2>
          <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Canonical subject::measure groups evaluated for cross-document comparability ({total} total)
          </p>
        </div>

        <button
          onClick={loadClusters}
          className={cn(
            'self-start sm:self-auto px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border',
            isDark
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
          )}
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh Clusters
        </button>
      </div>

      {/* Filter Toolbar */}
      <div
        className={cn(
          'p-4 rounded-xl border flex flex-col sm:flex-row gap-3',
          isDark ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
        )}
      >
        <div className="flex-1 relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search cluster key (e.g. india::real_gdp_growth)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={cn(
              'w-full pl-9 pr-3 py-2 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60',
              isDark
                ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
                : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
            )}
          />
        </div>

        <div
          className={cn(
            'flex items-center gap-2 px-3 py-2 rounded-lg border text-xs sm:w-56',
            isDark ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-200'
          )}
        >
          <span className={cn('text-[11px] whitespace-nowrap', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Min Cluster Size:
          </span>
          <select
            value={minSize}
            onChange={(e) => setMinSize(parseInt(e.target.value, 10))}
            className={cn(
              'bg-transparent font-mono focus:outline-none cursor-pointer w-full',
              isDark ? 'text-slate-200' : 'text-slate-700'
            )}
          >
            <option value={1}>{'All (>= 1 fact)'}</option>
            <option value={2}>{'Multi-Fact (>= 2 facts)'}</option>
            <option value={3}>{'Dense (>= 3 facts)'}</option>
          </select>
        </div>
      </div>

      {/* Clusters Grid */}
      {loading ? (
        <LoadingSkeleton rows={6} />
      ) : error ? (
        <div className="p-6 text-center bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs">
          {error}
        </div>
      ) : filteredClusters.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="No Clusters Found"
          description="No clusters matched your search criteria."
          action={{ label: 'Reset Filters', onClick: () => { setSearchQuery(''); setMinSize(1); } }}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredClusters.map((cluster) => {
            // cluster_key is Fact.cluster_key() from fact_layer/models.py,
            // always "<subject>::<measure>" — split on the first occurrence
            // in case a measure name itself ever contains "::".
            const sepIdx = cluster.cluster_key.indexOf('::');
            const subjectLabel = sepIdx >= 0 ? cluster.cluster_key.slice(0, sepIdx) : cluster.cluster_key;
            const measureLabel = sepIdx >= 0 ? cluster.cluster_key.slice(sepIdx + 2) : '';

            return (
              <div
                key={cluster.cluster_key}
                onClick={() => onSelectCluster && onSelectCluster(cluster)}
                className={cn(
                  'p-4 rounded-xl border cursor-pointer space-y-3 group transition-all',
                  isDark
                    ? 'bg-slate-900/70 border-slate-800 hover:border-sky-500/30 hover:bg-slate-900 hover:shadow-lg hover:shadow-sky-500/5'
                    : 'bg-white border-slate-200 hover:border-sky-300 hover:shadow-md shadow-sm'
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="space-y-0.5 truncate">
                    <span
                      className={cn(
                        'text-[10px] font-mono uppercase tracking-wider block',
                        isDark ? 'text-slate-500' : 'text-slate-400'
                      )}
                    >
                      {subjectLabel}
                    </span>
                    <h4
                      className={cn(
                        'font-mono text-sm font-bold transition-colors truncate',
                        isDark
                          ? 'text-slate-100 group-hover:text-sky-400'
                          : 'text-slate-800 group-hover:text-sky-600'
                      )}
                    >
                      {measureLabel}
                    </h4>
                  </div>

                  <span className="px-2 py-0.5 rounded text-xs font-mono font-bold bg-sky-500/10 text-sky-500 border border-sky-500/20 shrink-0">
                    {cluster.size} {cluster.size === 1 ? 'fact' : 'facts'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
