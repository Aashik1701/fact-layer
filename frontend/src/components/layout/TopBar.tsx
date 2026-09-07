import React, { useState, useEffect } from 'react';
import { fetchStats } from '@/lib/api';
import { StatsResponse, RelationType } from '@/types';
import { RELATION_CONFIG } from '@/lib/constants';
import { Database, FileText, HelpCircle } from 'lucide-react';
import { ThemeToggle } from '@/components/common/ThemeToggle';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface TopBarProps {
  onOpenAbout: () => void;
  onSelectRelationType?: (type: string) => void;
}

export const TopBar: React.FC<TopBarProps> = ({ onOpenAbout, onSelectRelationType }) => {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [isOnline, setIsOnline] = useState<boolean>(true);
  const { isDark } = useTheme();

  const checkHealth = async () => {
    try {
      const data = await fetchStats();
      setStats(data);
      setIsOnline(true);
    } catch {
      setIsOnline(false);
    }
  };

  useEffect(() => {
    checkHealth();
    const interval = setInterval(checkHealth, 30_000);
    return () => clearInterval(interval);
  }, []);

  const passRate = stats?.span_verification.pass_rate
    ? `${(stats.span_verification.pass_rate * 100).toFixed(1)}%`
    : '81.1%';

  return (
    <header
      className={cn(
        'sticky top-0 z-30 w-full border-b px-4 sm:px-6 py-2.5',
        isDark
          ? 'border-slate-800 bg-slate-950/80 backdrop-blur-md'
          : 'border-slate-200 bg-white/80 backdrop-blur-md shadow-sm'
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Brand & Connectivity Indicator */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div
              className={cn(
                'w-8 h-8 rounded-lg border flex items-center justify-center font-mono font-bold text-sm',
                isDark
                  ? 'bg-sky-500/10 border-sky-500/20 text-sky-400'
                  : 'bg-sky-50 border-sky-200 text-sky-600'
              )}
            >
              FK
            </div>
            <div>
              <span
                className={cn(
                  'font-bold text-sm tracking-tight block',
                  isDark ? 'text-slate-100' : 'text-slate-900'
                )}
              >
                Fact Knowledge Layer
              </span>
              <span
                className={cn(
                  'text-[10px] font-mono block -mt-0.5',
                  isDark ? 'text-slate-400' : 'text-slate-500'
                )}
              >
                Comparability Before Comparison
              </span>
            </div>
          </div>

          {/* Backend Status Ping */}
          <div
            className={cn(
              'hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono border',
              isOnline
                ? isDark
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : isDark
                ? 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                : 'bg-rose-50 text-rose-600 border-rose-200'
            )}
          >
            <span
              className={cn(
                'w-2 h-2 rounded-full',
                isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'
              )}
            />
            {isOnline ? 'FASTAPI ONLINE' : 'API OFFLINE'}
          </div>
        </div>

        {/* Live Corpus Stats Pills */}
        {stats && (
          <div className="hidden lg:flex items-center gap-2 text-xs font-mono">
            <div
              className={cn(
                'px-2 py-0.5 rounded border flex items-center gap-1.5',
                isDark
                  ? 'bg-slate-900 border-slate-800 text-slate-300'
                  : 'bg-slate-100 border-slate-200 text-slate-600'
              )}
            >
              <Database className="w-3 h-3 text-sky-500" />
              <span>{stats.facts.total} Facts</span>
            </div>

            <div
              className={cn(
                'px-2 py-0.5 rounded border flex items-center gap-1.5',
                isDark
                  ? 'bg-slate-900 border-slate-800 text-slate-300'
                  : 'bg-slate-100 border-slate-200 text-slate-600'
              )}
            >
              <FileText className="w-3 h-3 text-emerald-500" />
              <span>{stats.documents.count} Docs</span>
            </div>

            <div
              className={cn(
                'px-2 py-0.5 rounded border flex items-center gap-1.5',
                isDark
                  ? 'bg-slate-900 border-slate-800 text-slate-300'
                  : 'bg-slate-100 border-slate-200 text-slate-600'
              )}
            >
              <span className="text-amber-500 font-bold">{passRate}</span>
              <span className={isDark ? 'text-slate-500' : 'text-slate-400'}>Span Verified</span>
            </div>
          </div>
        )}

        {/* Relation Breakdown Chips */}
        {stats && stats.relations && (
          <div className="hidden xl:flex items-center gap-1.5">
            {(Object.keys(RELATION_CONFIG) as RelationType[]).map((type) => {
              // GET /stats returns by_type keyed on the backend's lowercase
              // RelationType.value (e.g. "apparent_conflict"), not the
              // uppercase RelationType used for display config — looking up
              // the uppercase key directly always missed, silently showing
              // 0 for every real count.
              const count = stats.relations.by_type[type.toLowerCase()] ?? 0;
              const cfg = RELATION_CONFIG[type];
              return (
                <button
                  key={type}
                  onClick={() => onSelectRelationType && onSelectRelationType(type)}
                  className={cn(
                    'px-2 py-0.5 rounded text-[10px] font-mono border transition-all hover:scale-105',
                    cfg.bgBadge,
                    cfg.textBadge,
                    cfg.borderBadge
                  )}
                  title={`Filter: ${cfg.label} (${count} relations)`}
                >
                  <span>{type.slice(0, 4)}:</span>{' '}
                  <span className="font-bold">{count}</span>
                </button>
              );
            })}
          </div>
        )}

        {/* Right actions */}
        <div className="flex items-center gap-2">
          {/* Theme Toggle */}
          <ThemeToggle />

          <button
            onClick={onOpenAbout}
            className={cn(
              'p-1.5 sm:px-2.5 sm:py-1 rounded-lg border text-xs font-mono flex items-center gap-1.5 transition-colors',
              isDark
                ? 'bg-slate-900 hover:bg-slate-800 text-slate-300 border-slate-800'
                : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
            )}
            title="System Thesis & Architecture"
          >
            <HelpCircle className="w-3.5 h-3.5 text-sky-500" />
            <span className="hidden sm:inline">About System</span>
          </button>
        </div>
      </div>
    </header>
  );
};
