import React from 'react';
import { LucideIcon, Inbox } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
}) => {
  const { isDark } = useTheme();

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center p-12 text-center rounded-xl border border-dashed',
        isDark
          ? 'border-slate-800 bg-slate-900/30'
          : 'border-slate-200 bg-slate-50',
        className
      )}
    >
      <div
        className={cn(
          'w-12 h-12 rounded-full flex items-center justify-center mb-4',
          isDark ? 'bg-slate-800/80 text-slate-400' : 'bg-slate-100 text-slate-400'
        )}
      >
        <Icon className="w-6 h-6" />
      </div>
      <h3 className={cn('text-base font-semibold', isDark ? 'text-slate-200' : 'text-slate-700')}>
        {title}
      </h3>
      <p className={cn('text-sm max-w-sm mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
        {description}
      </p>
      {action && (
        <button
          onClick={action.onClick}
          className={cn(
            'mt-4 px-4 py-2 text-sm font-medium rounded-lg border transition-colors',
            isDark
              ? 'text-sky-400 bg-sky-500/10 border-sky-500/20 hover:bg-sky-500/20'
              : 'text-sky-600 bg-sky-50 border-sky-200 hover:bg-sky-100'
          )}
        >
          {action.label}
        </button>
      )}
    </div>
  );
};
