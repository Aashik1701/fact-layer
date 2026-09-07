import React, { useState } from 'react';
import { TopBar } from './TopBar';
import { Sidebar, NavTab } from './Sidebar';
import { AboutModal } from '@/components/common/AboutModal';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard,
  GitCompare,
  Database,
  FileText,
  Sparkles,
  ShieldAlert,
  Layers,
} from 'lucide-react';

interface AppShellProps {
  currentTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  children: React.ReactNode;
  onSelectRelationType?: (type: string) => void;
}

export const AppShell: React.FC<AppShellProps> = ({
  currentTab,
  onTabChange,
  children,
  onSelectRelationType,
}) => {
  const [isAboutOpen, setIsAboutOpen] = useState<boolean>(false);
  const { isDark } = useTheme();

  const mobileNavItems = [
    { id: 'overview',  label: 'Overview',   icon: LayoutDashboard },
    { id: 'relations', label: 'Relations',   icon: GitCompare },
    { id: 'facts',     label: 'Facts',       icon: Database },
    { id: 'documents', label: 'Docs',        icon: FileText },
    { id: 'cases',     label: 'Cases',       icon: Sparkles },
    { id: 'rejected',  label: 'QC',          icon: ShieldAlert },
    { id: 'clusters',  label: 'Clusters',    icon: Layers },
  ];

  return (
    <div
      className={cn(
        'min-h-screen flex flex-col font-sans selection:bg-sky-500/20 selection:text-sky-300',
        isDark ? 'bg-slate-950 text-slate-100' : 'bg-slate-50 text-slate-900'
      )}
    >
      {/* Top Header */}
      <TopBar
        onOpenAbout={() => setIsAboutOpen(true)}
        onSelectRelationType={(type) => {
          onTabChange('relations');
          if (onSelectRelationType) onSelectRelationType(type);
        }}
      />

      {/* Main Container */}
      <div className="flex-1 flex w-full">
        {/* Desktop Sidebar */}
        <div className="hidden md:block">
          <Sidebar
            currentTab={currentTab}
            onTabChange={onTabChange}
            onOpenAbout={() => setIsAboutOpen(true)}
          />
        </div>

        {/* Dynamic Page Content */}
        <main className="flex-1 p-4 sm:p-6 md:p-8 max-w-7xl mx-auto w-full pb-20 md:pb-8">
          {children}
        </main>
      </div>

      {/* Mobile Bottom Navigation Bar */}
      <nav
        className={cn(
          'md:hidden fixed bottom-0 left-0 right-0 z-40 border-t flex items-center justify-around py-2 px-1',
          isDark
            ? 'bg-slate-950/90 backdrop-blur-lg border-slate-800'
            : 'bg-white/90 backdrop-blur-lg border-slate-200 shadow-lg'
        )}
      >
        {mobileNavItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onTabChange(item.id as NavTab)}
              className={cn(
                'flex flex-col items-center gap-1 p-1.5 rounded-lg text-[10px] font-mono transition-colors',
                isActive
                  ? isDark ? 'text-sky-400 font-bold' : 'text-sky-600 font-bold'
                  : isDark ? 'text-slate-400 hover:text-slate-200' : 'text-slate-400 hover:text-slate-700'
              )}
            >
              <Icon className="w-4 h-4" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* About Modal */}
      <AboutModal isOpen={isAboutOpen} onClose={() => setIsAboutOpen(false)} />
    </div>
  );
};
