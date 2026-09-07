import React from 'react';
import { RelationType } from '@/types';
import { RELATION_CONFIG } from '@/lib/constants';
import { cn } from '@/lib/utils';
import { CheckCircle2, XCircle, AlertTriangle, RefreshCw, GitFork } from 'lucide-react';

interface GateVerdictBadgeProps {
  verdict: RelationType | string;
  size?: 'sm' | 'md' | 'lg';
  showIcon?: boolean;
  className?: string;
}

export const GateVerdictBadge: React.FC<GateVerdictBadgeProps> = ({
  verdict,
  size = 'md',
  showIcon = true,
  className,
}) => {
  const normVerdict = (verdict ? verdict.toUpperCase().replace(/\s+/g, '_') : 'APPARENT_CONFLICT') as RelationType;
  const config = RELATION_CONFIG[normVerdict] || {
    label: verdict,
    description: '',
    bgBadge: 'bg-slate-800',
    textBadge: 'text-slate-300',
    borderBadge: 'border-slate-700',
  };

  const getIcon = () => {
    switch (normVerdict) {
      case 'CORROBORATES':
        return <CheckCircle2 className="shrink-0" />;
      case 'CONTRADICTS':
        return <XCircle className="shrink-0" />;
      case 'APPARENT_CONFLICT':
        return <AlertTriangle className="shrink-0" />;
      case 'SUPERSEDES':
        return <RefreshCw className="shrink-0" />;
      case 'AGGREGATES_INTO':
        return <GitFork className="shrink-0" />;
      default:
        return null;
    }
  };

  const sizeClasses = {
    sm: 'text-[11px] px-2 py-0.5 gap-1 [&>svg]:w-3 [&>svg]:h-3',
    md: 'text-xs px-2.5 py-1 gap-1.5 [&>svg]:w-3.5 [&>svg]:h-3.5',
    lg: 'text-sm px-3.5 py-1.5 gap-2 [&>svg]:w-4 [&>svg]:h-4',
  }[size];

  return (
    <span
      className={cn(
        'inline-flex items-center font-mono font-medium rounded-md border tracking-wide uppercase shadow-sm',
        config.bgBadge,
        config.textBadge,
        config.borderBadge,
        sizeClasses,
        className
      )}
      title={config.description}
    >
      {showIcon && getIcon()}
      <span>{config.label || verdict}</span>
    </span>
  );
};
