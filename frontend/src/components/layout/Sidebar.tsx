import React, { useEffect, useState } from 'react';
import {
  LayoutDashboard,
  GitCompare,
  Database,
  FileText,
  Sparkles,
  ShieldAlert,
  Layers,
  PanelLeftClose,
  PanelLeftOpen,
  Info,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { fetchRejectedFacts } from '@/lib/api';

const COLLAPSE_STORAGE_KEY = 'fact_layer_sidebar_collapsed';

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
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSE_STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let cancelled = false;
    fetchRejectedFacts(1)
      .then((res) => { if (!cancelled) setRejectedTotal(res.total); })
      .catch(() => { if (!cancelled) setRejectedTotal(null); });
    return () => { cancelled = true; };
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, String(next));
      } catch {
        // localStorage unavailable (private browsing, etc.) — collapse still
        // works for this session, it just won't persist across reloads.
      }
      return next;
    });
  };

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
        'border-r p-4 flex flex-col justify-between shrink-0 h-[calc(100vh-53px)] sticky top-[53px] transition-[width] duration-200 ease-in-out overflow-hidden',
        collapsed ? 'w-[68px] px-2' : 'w-64',
        isDark
          ? 'border-slate-800 bg-slate-950/60'
          : 'border-slate-200 bg-slate-50'
      )}
    >
      <div className="space-y-1">
        <div className={cn('flex items-center mb-1', collapsed ? 'justify-center' : 'justify-between px-1')}>
          {!collapsed && (
            <span
              className={cn(
                'text-[10px] font-mono uppercase tracking-wider font-semibold',
                isDark ? 'text-slate-500' : 'text-slate-400'
              )}
            >
              Platform Navigation
            </span>
          )}
          <button
            type="button"
            onClick={toggleCollapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={cn(
              'p-1.5 rounded-lg transition-colors shrink-0',
              isDark
                ? 'text-slate-500 hover:text-slate-300 hover:bg-slate-900'
                : 'text-slate-400 hover:text-slate-700 hover:bg-slate-100'
            )}
          >
            {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        </div>

        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;

          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id as NavTab)}
              title={collapsed ? item.label : undefined}
              aria-label={item.label}
              className={cn(
                'w-full flex items-center rounded-xl text-xs font-medium transition-all group border',
                collapsed ? 'justify-center px-0 py-2.5' : 'justify-between px-3 py-2.5',
                isActive
                  ? isDark
                    ? 'bg-sky-500/10 text-sky-400 font-semibold border-sky-500/20 shadow-sm'
                    : 'bg-sky-50 text-sky-700 font-semibold border-sky-200 shadow-sm'
                  : isDark
                  ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-900 border-transparent'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100 border-transparent'
              )}
            >
              <div className={cn('flex items-center', !collapsed && 'gap-2.5')}>
                <Icon
                  className={cn(
                    'w-4 h-4 transition-colors shrink-0',
                    isActive
                      ? isDark ? 'text-sky-400' : 'text-sky-600'
                      : isDark ? 'text-slate-500 group-hover:text-slate-300' : 'text-slate-400 group-hover:text-slate-600'
                  )}
                />
                {!collapsed && <span>{item.label}</span>}
              </div>

              {!collapsed && item.badge && (
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

      {/* Thesis Footer Callout — collapses to a single icon button so the
          "About" affordance stays reachable without the full card. */}
      {collapsed ? (
        <button
          type="button"
          onClick={onOpenAbout}
          title="Read Architecture Thesis"
          aria-label="Read Architecture Thesis"
          className={cn(
            'p-2.5 rounded-xl border flex items-center justify-center transition-colors',
            isDark
              ? 'bg-slate-900/60 border-slate-800/80 text-emerald-400 hover:bg-slate-900'
              : 'bg-sky-50 border-sky-100 text-emerald-600 hover:bg-sky-100'
          )}
        >
          <Info className="w-4 h-4" />
        </button>
      ) : (
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
      )}
    </aside>
  );
};
