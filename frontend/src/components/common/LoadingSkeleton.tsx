import React from 'react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

export const LoadingSkeleton: React.FC<{ className?: string; rows?: number }> = ({
  className,
  rows = 3,
}) => {
  const { isDark } = useTheme();

  return (
    <div className={cn('space-y-3 animate-pulse', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className={cn(
            'h-12 rounded-lg w-full border',
            isDark
              ? 'bg-slate-800/60 border-slate-700/30'
              : 'bg-slate-100 border-slate-200/60'
          )}
          style={{ opacity: 1 - i * 0.15 }}
        />
      ))}
    </div>
  );
};

export const CardSkeleton: React.FC<{ count?: number }> = ({ count = 4 }) => {
  const { isDark } = useTheme();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={cn(
            'h-28 rounded-xl border p-5 space-y-3 animate-pulse',
            isDark
              ? 'bg-slate-800/40 border-slate-700/30'
              : 'bg-slate-100 border-slate-200'
          )}
        />
      ))}
    </div>
  );
};
