import React from 'react';
import { Modal } from '@/components/common/Modal';
import { FactSummary } from '@/types';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { FileSearch } from 'lucide-react';
import { formatIssuer } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface FactDetailDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  fact: FactSummary | null;
  onOpenEvidence: (fact: FactSummary) => void;
}

export const FactDetailDrawer: React.FC<FactDetailDrawerProps> = ({
  isOpen,
  onClose,
  fact,
  onOpenEvidence,
}) => {
  const { isDark } = useTheme();
  if (!fact) return null;

  const cardCls = cn(
    'p-4 rounded-xl border space-y-1',
    isDark ? 'bg-slate-900 border-slate-800' : 'bg-slate-50 border-slate-200'
  );

  const labelCls = cn(
    'text-[10px] font-mono uppercase tracking-wider block',
    isDark ? 'text-slate-500' : 'text-slate-400'
  );

  const valueCls = cn(
    'font-mono block mt-0.5',
    isDark ? 'text-slate-200' : 'text-slate-800'
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2">
          <span className="font-mono text-base font-bold text-sky-500">
            {fact.subject}::{fact.measure}
          </span>
          <span
            className={cn(
              'px-2 py-0.5 rounded text-[11px] font-mono',
              isDark ? 'bg-slate-800 text-slate-400' : 'bg-slate-100 text-slate-500'
            )}
          >
            {fact.fact_id.slice(0, 8)}
          </span>
        </div>
      }
      subtitle="Canonicalized knowledge layer entity attributes and source grounding"
      maxWidth="3xl"
    >
      <div className="space-y-6 text-xs">
        {/* Values Comparison Strip */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className={cardCls}>
            <span className={labelCls}>Canonical Normalized Value</span>
            <div className="text-xl font-mono font-bold text-emerald-500">
              {fact.value.normalized}{' '}
              <span className={cn('text-sm font-normal', isDark ? 'text-slate-400' : 'text-slate-500')}>
                {fact.value.unit || ''}
              </span>
            </div>
            {fact.value.currency && (
              <span className={cn('text-[11px] font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
                Currency: {fact.value.currency}
              </span>
            )}
          </div>

          <div className={cardCls}>
            <span className={labelCls}>Raw Stated Text (In Source PDF)</span>
            <div className={cn('text-base font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
              "{fact.value.raw}"
            </div>
            <span className={cn('text-[11px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
              Unmodified text string before normalization
            </span>
          </div>
        </div>

        {/* Qualifiers & Metadata Grid */}
        <div
          className={cn(
            'p-4 rounded-xl border space-y-3',
            isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <span
            className={cn(
              'text-[11px] font-semibold uppercase tracking-wider block border-b pb-2',
              isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
            )}
          >
            Entity Qualifiers &amp; Modality Taxonomy
          </span>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <div>
              <span className={labelCls}>Modality</span>
              <span
                className={cn(
                  'px-2 py-0.5 rounded font-mono border inline-block mt-0.5 text-[11px]',
                  isDark
                    ? 'bg-slate-800 text-slate-200 border-slate-700'
                    : 'bg-slate-100 text-slate-700 border-slate-200'
                )}
              >
                {fact.modality}
              </span>
            </div>
            <div>
              <span className={labelCls}>Confidence</span>
              <div className="mt-0.5">
                <ConfidencePill confidence={fact.confidence} showIcon />
              </div>
            </div>
            <div>
              <span className={labelCls}>Period</span>
              <span className={valueCls}>
                {fact.qualifiers.period?.label ||
                  `${fact.qualifiers.period?.start || ''} - ${fact.qualifiers.period?.end || ''}`.trim() ||
                  'N/A'}
              </span>
            </div>
            <div>
              <span className={labelCls}>Reporting Issuer</span>
              <span className={cn(valueCls, 'font-sans font-medium')}>
                {formatIssuer(fact.qualifiers.issuer)}
              </span>
            </div>
            <div>
              <span className={labelCls}>Institutional Scope</span>
              <span className={valueCls}>{fact.qualifiers.scope || 'default'}</span>
            </div>
            <div>
              <span className={labelCls}>Measurement Basis</span>
              <span className={valueCls}>{fact.qualifiers.basis || 'standard'}</span>
            </div>
          </div>
        </div>

        {/* Provenance Banner */}
        <div
          className={cn(
            'p-4 rounded-xl border flex items-center justify-between gap-4',
            isDark ? 'bg-slate-900 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <div className="space-y-0.5 min-w-0">
            <span className={labelCls}>Document Provenance</span>
            <div
              className={cn(
                'font-mono text-xs truncate max-w-sm',
                isDark ? 'text-slate-200' : 'text-slate-700'
              )}
            >
              {fact.doc_id || 'Unknown Document'}
            </div>
            <span className={cn('text-[11px]', isDark ? 'text-slate-400' : 'text-slate-500')}>
              PDF Page {fact.page ?? 1}
            </span>
          </div>

          <button
            onClick={() => { onClose(); onOpenEvidence(fact); }}
            className="px-4 py-2 rounded-lg bg-sky-500 hover:bg-sky-400 text-white font-semibold font-mono text-xs flex items-center gap-1.5 shadow-md shadow-sky-500/20 transition-all shrink-0"
          >
            <FileSearch className="w-4 h-4" />
            Inspect PDF Evidence
          </button>
        </div>
      </div>
    </Modal>
  );
};
