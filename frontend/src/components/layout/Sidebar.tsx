import React, { useEffect, useState } from 'react';
import {
  LayoutDashboard,
  GitCompare,
  Database,
  FileText,
  Sparkles,
  ShieldAlert,
  Layers,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { fetchRejectedFacts } from '@/lib/api';

export type NavTab =
  | 'overview'
  | 'relations'
  | 'facts'
  | 'documents'
  | 'cases'
  | 'rejected'
  | 'clusters';

interface SidebarProps {
  currentTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  onOpenAbout: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onTabChange,
  onOpenAbout,
}) => {
  const { isDark } = useTheme();
  const [rejectedTotal, setRejectedTotal] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRejectedFacts(1)
      .then((res) => { if (!cancelled) setRejectedTotal(res.total); })
      .catch(() => { if (!cancelled) setRejectedTotal(null); });
    return () => { cancelled = true; };
  }, []);

  const navItems = [
    { id: 'overview',   label: 'Overview',            icon: LayoutDashboard },
    { id: 'relations',  label: 'Relations Explorer',   icon: GitCompare,  badge: 'Gate' },
    { id: 'facts',      label: 'Facts Explorer',       icon: Database },
    { id: 'documents',  label: 'Documents & Ingest',   icon: FileText },
    { id: 'cases',      label: '4 Required Cases',     icon: Sparkles },
    { id: 'rejected',   label: 'Rejected Facts',       icon: ShieldAlert, badge: rejectedTotal !== null ? String(rejectedTotal) : undefined },
    { id: 'clusters',   label: 'Clusters',             icon: Layers },
  ];

  return (
    <aside
      className={cn(
        'w-64 border-r p-4 flex flex-col justify-between shrink-0 h-[calc(100vh-53px)] sticky top-[53px]',
        isDark
          ? 'border-slate-800 bg-slate-950/60'
          : 'border-slate-200 bg-slate-50'
      )}
    >
      <div className="space-y-1">
        <div
          className={cn(
            'px-3 py-2 text-[10px] font-mono uppercase tracking-wider font-semibold',
            isDark ? 'text-slate-500' : 'text-slate-400'
          )}
        >
          Platform Navigation
        </div>

        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;

          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id as NavTab)}
              className={cn(
                'w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium transition-all group border',
                isActive
                  ? isDark
                    ? 'bg-sky-500/10 text-sky-400 font-semibold border-sky-500/20 shadow-sm'
                    : 'bg-sky-50 text-sky-700 font-semibold border-sky-200 shadow-sm'
                  : isDark
                  ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-900 border-transparent'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100 border-transparent'
              )}
            >
              <div className="flex items-center gap-2.5">
                <Icon
                  className={cn(
                    'w-4 h-4 transition-colors',
                    isActive
                      ? isDark ? 'text-sky-400' : 'text-sky-600'
                      : isDark ? 'text-slate-500 group-hover:text-slate-300' : 'text-slate-400 group-hover:text-slate-600'
                  )}
                />
                <span>{item.label}</span>
              </div>

              {item.badge && (
                <span
                  className={cn(
                    'px-1.5 py-0.5 rounded text-[10px] font-mono border',
                    isActive
                      ? isDark
                        ? 'bg-sky-500/20 text-sky-300 border-sky-500/30'
                        : 'bg-sky-100 text-sky-600 border-sky-200'
                      : isDark
                      ? 'bg-slate-900 text-slate-500 border-slate-800'
                      : 'bg-slate-100 text-slate-400 border-slate-200'
                  )}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Thesis Footer Callout */}
      <div
        className={cn(
          'p-3 rounded-xl border space-y-2',
          isDark ? 'bg-slate-900/60 border-slate-800/80' : 'bg-sky-50 border-sky-100'
        )}
      >
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
          <span className={cn('text-[11px] font-mono', isDark ? 'text-emerald-400' : 'text-emerald-600')}>
            Deterministic Engine
          </span>
        </div>
        <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Enforcing comparability across source qualifiers prior to document comparison.
        </p>
        <button
          onClick={onOpenAbout}
          className={cn(
            'text-[11px] font-mono block transition-colors',
            isDark ? 'text-sky-400 hover:text-sky-300' : 'text-sky-600 hover:text-sky-500'
          )}
        >
          Read Architecture Thesis →
        </button>
      </div>
    </aside>
  );
};
