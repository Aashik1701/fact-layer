import React from 'react';
import { cn } from '@/lib/utils';
import { ShieldAlert, ShieldCheck } from 'lucide-react';

interface ConfidencePillProps {
  confidence: number | null | undefined;
  showIcon?: boolean;
  className?: string;
}

export const ConfidencePill: React.FC<ConfidencePillProps> = ({
  confidence,
  showIcon = false,
  className,
}) => {
  if (confidence === null || confidence === undefined) {
    return (
      <span
        className={cn(
          'px-2 py-0.5 rounded text-xs font-mono border',
          'bg-slate-200 text-slate-500 border-slate-300 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700',
          className
        )}
      >
        N/A
      </span>
    );
  }

  const pct = Math.round(confidence * 100);
  let colorClasses = 'bg-rose-500/10 text-rose-500 border-rose-400/40 dark:text-rose-400 dark:border-rose-500/30';
  let Icon = ShieldAlert;

  if (confidence >= 0.85) {
    colorClasses =
      'bg-emerald-500/10 text-emerald-600 border-emerald-400/40 dark:text-emerald-400 dark:border-emerald-500/30';
    Icon = ShieldCheck;
  } else if (confidence >= 0.60) {
    colorClasses =
      'bg-amber-500/10 text-amber-600 border-amber-400/40 dark:text-amber-400 dark:border-amber-500/30';
    Icon = ShieldCheck;
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono font-medium border',
        colorClasses,
        className
      )}
      title={`Confidence Score: ${(confidence * 100).toFixed(1)}%`}
    >
      {showIcon && <Icon className="w-3 h-3" />}
      {pct}%
    </span>
  );
};
