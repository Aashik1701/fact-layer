import React from 'react';
import { ConfidenceLabel } from '@/lib/confidenceLabel';
import { cn } from '@/lib/utils';
import { ShieldCheck, ShieldQuestion, HelpCircle, Ban, XCircle, ShieldAlert } from 'lucide-react';

interface UncertaintyBadgeProps {
  label: ConfidenceLabel;
  detail?: string;
  size?: 'sm' | 'md';
  className?: string;
}

// Deliberately NOT all green: CONFIDENT is the only state that reads as
// unambiguous success. Every other state is styled to visually communicate
// that something needs the reviewer's attention.
const STYLE: Record<ConfidenceLabel, { classes: string; icon: React.ElementType }> = {
  CONFIDENT: { classes: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30', icon: ShieldCheck },
  SUPPORTED: { classes: 'bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30', icon: ShieldCheck },
  'PARTIALLY VERIFIED': { classes: 'bg-slate-500/10 text-slate-600 dark:text-slate-300 border-slate-500/30', icon: ShieldQuestion },
  INCOMPARABLE: { classes: 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30', icon: Ban },
  AMBIGUOUS: { classes: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30', icon: HelpCircle },
  REJECTED: { classes: 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30', icon: XCircle },
};

export const UncertaintyBadge: React.FC<UncertaintyBadgeProps> = ({ label, detail, size = 'md', className }) => {
  const { classes, icon: Icon } = STYLE[label] || { classes: 'bg-slate-500/10 text-slate-500 border-slate-500/30', icon: ShieldAlert };
  const sizeClasses = size === 'sm' ? 'text-[10px] px-1.5 py-0.5 gap-1' : 'text-[11px] px-2 py-0.5 gap-1.5';

  return (
    <span
      className={cn(
        'inline-flex items-center font-mono font-semibold uppercase tracking-wide rounded-md border',
        classes,
        sizeClasses,
        className
      )}
      title={detail}
    >
      <Icon className={size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5'} />
      {label}
    </span>
  );
};
