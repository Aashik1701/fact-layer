import React, { useEffect, useState } from 'react';
import { Modal } from '@/components/common/Modal';
import { GateVerdictBadge } from './GateVerdictBadge';
import { QualifierDiffTable } from './QualifierDiffTable';
import { fetchRelation } from '@/lib/api';
import { RelationFull, FactFull, FactSummary } from '@/types';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { getCaveatExplanation } from '@/lib/constants';
import {
  FileSearch,
  Cpu,
  Info,
  Layers,
} from 'lucide-react';
import { formatIssuer, cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface RelationInspectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  relationId: string | null;
  onOpenEvidence: (fact: FactFull | FactSummary) => void;
}

export const RelationInspectorModal: React.FC<RelationInspectorModalProps> = ({
  isOpen,
  onClose,
  relationId,
  onOpenEvidence,
}) => {
  const { isDark } = useTheme();
  const [relation, setRelation] = useState<RelationFull | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !relationId) {
      setRelation(null);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);
    fetchRelation(relationId)
      .then((data) => setRelation(data))
      .catch((err) => {
        console.error('Failed to load relation details:', err);
        setError('Failed to load relation from knowledge store.');
      })
      .finally(() => setLoading(false));
  }, [isOpen, relationId]);

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-sky-500/10 border border-sky-500/20 text-sky-500">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className={cn('text-base font-bold', isDark ? 'text-slate-100' : 'text-slate-900')}>
                Cross-Fact Relation Inspector
              </h3>
              {relation && (
                <span
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] font-mono border',
                    isDark
                      ? 'bg-slate-800 text-slate-300 border-slate-700'
                      : 'bg-slate-100 text-slate-600 border-slate-200'
                  )}
                >
                  {relation.relation_id}
                </span>
              )}
            </div>
            <p className={cn('text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Deterministic comparability gate evaluation and qualifier alignment chain
            </p>
          </div>
        </div>
      }
      maxWidth="5xl"
    >
      {loading ? (
        <div className="py-16 flex flex-col items-center justify-center space-y-3">
          <div className="w-8 h-8 border-2 border-sky-500 border-t-transparent rounded-full animate-spin" />
          <span className={cn('text-xs font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Resolving relation and evaluating gate qualifiers...
          </span>
        </div>
      ) : error ? (
        <div className={cn(
          'p-8 text-center rounded-xl text-sm border',
          isDark
            ? 'bg-rose-500/10 border-rose-500/20 text-rose-300'
            : 'bg-rose-50 border-rose-200 text-rose-700'
        )}>
          {error}
        </div>
      ) : relation ? (
        <div className="space-y-6">
          {/* Top Verdict Strip */}
          <div
            className={cn(
              'p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-4',
              isDark ? 'bg-slate-800/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            )}
          >
            <div className="flex items-center gap-3">
              <GateVerdictBadge verdict={relation.relation} size="lg" />
              <div>
                <div className={cn('text-xs font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  DECIDED BY:{' '}
                  <span className={cn('uppercase font-semibold', isDark ? 'text-slate-200' : 'text-slate-800')}>
                    {relation.decided_by}
                  </span>
                </div>
                {relation.reason_code && (
                  <div className="text-[11px] font-mono text-amber-500 mt-0.5 font-medium">
                    Trigger: {relation.reason_code}
                  </div>
                )}
                {relation.gate && relation.gate.verdict !== 'comparable' && (
                  <div
                    className={cn('text-[11px] font-mono mt-0.5', isDark ? 'text-slate-400' : 'text-slate-500')}
                    title="The comparability gate's own raw verdict, independent of how adjudication ultimately classified the relationship"
                  >
                    Comparability Gate:{' '}
                    <span className="uppercase font-semibold text-indigo-400">{relation.gate.verdict}</span>
                    {relation.gate.cross_issuer && ' (cross-issuer)'}
                  </div>
                )}
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="text-right">
                <span className={cn('text-[10px] uppercase font-mono block', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  Relation Confidence
                </span>
                <div className="flex items-center justify-end gap-2 mt-0.5">
                  <ConfidencePill confidence={relation.confidence} showIcon />
                  {relation.relation === 'CONTRADICTS' && (
                    <span className="text-[10px] text-rose-500 font-mono font-medium" title="Halved due to contradiction rule">
                      (Adjudication Halved)
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Explanation Banner */}
          {relation.explanation && (
            <div
              className={cn(
                'p-4 rounded-xl border text-xs leading-relaxed space-y-1.5',
                isDark ? 'bg-slate-800/40 border-slate-800 text-slate-300' : 'bg-sky-50/50 border-sky-100 text-slate-700'
              )}
            >
              <div className="flex items-center gap-1.5 text-sky-500 font-semibold uppercase tracking-wider text-[11px]">
                <Info className="w-3.5 h-3.5" />
                Deterministic Adjudication Explanation
              </div>
              <p className={cn('font-sans', isDark ? 'text-slate-200' : 'text-slate-800')}>{relation.explanation}</p>
              {relation.reason_code && (
                <p className={cn('text-[11px] font-mono', isDark ? 'text-amber-400/90' : 'text-amber-700')}>
                  Contextual Caveat: {getCaveatExplanation(relation.reason_code)}
                </p>
              )}
            </div>
          )}

          {/* Side-by-Side Fact Comparison Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Fact A Card */}
            {relation.source_fact ? (
              <div
                className={cn(
                  'p-4 rounded-xl border space-y-3 relative',
                  isDark ? 'bg-slate-800/50 border-slate-800' : 'bg-slate-50/80 border-slate-200'
                )}
              >
                <div className={cn('flex items-center justify-between border-b pb-2', isDark ? 'border-slate-800' : 'border-slate-200')}>
                  <span className="text-xs font-mono font-bold text-sky-500 uppercase tracking-wide">
                    Source Fact A
                  </span>
                  <ConfidencePill confidence={relation.source_fact.confidence} />
                </div>

                <div className="space-y-1.5 text-xs">
                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>SUBJECT & MEASURE</span>
                    <span className={cn('font-mono font-semibold', isDark ? 'text-slate-100' : 'text-slate-900')}>
                      {relation.source_fact.subject}::{relation.source_fact.measure}
                    </span>
                  </div>

                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>CANONICAL VALUE</span>
                    <span className="font-mono text-emerald-500 text-base font-bold">
                      {relation.source_fact.value.normalized} {relation.source_fact.value.unit || ''}
                    </span>
                  </div>

                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>RAW STATED VALUE</span>
                    <span className={cn('font-mono text-xs', isDark ? 'text-slate-300' : 'text-slate-600')}>
                      "{relation.source_fact.value.raw}"
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>MODALITY</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{relation.source_fact.modality}</span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>PERIOD</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
                        {relation.source_fact.qualifiers.period?.label || 'N/A'}
                      </span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>ISSUER</span>
                      <span className={cn('truncate block', isDark ? 'text-slate-300' : 'text-slate-700')}>
                        {formatIssuer(relation.source_fact.qualifiers.issuer)}
                      </span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>LOCATION</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
                        Page {relation.source_fact.page ?? 1}
                      </span>
                    </div>
                  </div>
                </div>

                <button
                  onClick={() => onOpenEvidence(relation.source_fact!)}
                  className={cn(
                    'w-full mt-2 py-2 px-3 rounded-lg border text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors',
                    isDark
                      ? 'bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 border-sky-500/20'
                      : 'bg-sky-50 hover:bg-sky-100 text-sky-700 border-sky-200'
                  )}
                >
                  <FileSearch className="w-3.5 h-3.5" />
                  View PDF Evidence Provenance
                </button>
              </div>
            ) : (
              <div className={cn('p-4 rounded-xl border text-xs', isDark ? 'bg-slate-800/40 border-slate-800 text-slate-500' : 'bg-slate-50 border-slate-200 text-slate-400')}>
                Source Fact not available in store
              </div>
            )}

            {/* Fact B Card */}
            {relation.target_fact ? (
              <div
                className={cn(
                  'p-4 rounded-xl border space-y-3 relative',
                  isDark ? 'bg-slate-800/50 border-slate-800' : 'bg-slate-50/80 border-slate-200'
                )}
              >
                <div className={cn('flex items-center justify-between border-b pb-2', isDark ? 'border-slate-800' : 'border-slate-200')}>
                  <span className="text-xs font-mono font-bold text-indigo-500 uppercase tracking-wide">
                    Source Fact B
                  </span>
                  <ConfidencePill confidence={relation.target_fact.confidence} />
                </div>

                <div className="space-y-1.5 text-xs">
                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>SUBJECT & MEASURE</span>
                    <span className={cn('font-mono font-semibold', isDark ? 'text-slate-100' : 'text-slate-900')}>
                      {relation.target_fact.subject}::{relation.target_fact.measure}
                    </span>
                  </div>

                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>CANONICAL VALUE</span>
                    <span className="font-mono text-emerald-500 text-base font-bold">
                      {relation.target_fact.value.normalized} {relation.target_fact.value.unit || ''}
                    </span>
                  </div>

                  <div>
                    <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>RAW STATED VALUE</span>
                    <span className={cn('font-mono text-xs', isDark ? 'text-slate-300' : 'text-slate-600')}>
                      "{relation.target_fact.value.raw}"
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>MODALITY</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{relation.target_fact.modality}</span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>PERIOD</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
                        {relation.target_fact.qualifiers.period?.label || 'N/A'}
                      </span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>ISSUER</span>
                      <span className={cn('truncate block', isDark ? 'text-slate-300' : 'text-slate-700')}>
                        {formatIssuer(relation.target_fact.qualifiers.issuer)}
                      </span>
                    </div>
                    <div>
                      <span className={cn('block text-[10px]', isDark ? 'text-slate-400' : 'text-slate-500')}>LOCATION</span>
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
                        Page {relation.target_fact.page ?? 1}
                      </span>
                    </div>
                  </div>
                </div>

                <button
                  onClick={() => onOpenEvidence(relation.target_fact!)}
                  className={cn(
                    'w-full mt-2 py-2 px-3 rounded-lg border text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors',
                    isDark
                      ? 'bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 border-indigo-500/20'
                      : 'bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border-indigo-200'
                  )}
                >
                  <FileSearch className="w-3.5 h-3.5" />
                  View PDF Evidence Provenance
                </button>
              </div>
            ) : (
              <div className={cn('p-4 rounded-xl border text-xs', isDark ? 'bg-slate-800/40 border-slate-800 text-slate-500' : 'bg-slate-50 border-slate-200 text-slate-400')}>
                Target Fact not available in store
              </div>
            )}
          </div>

          {/* Qualifier Diff Matrix */}
          {relation.source_fact && relation.target_fact && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className={cn('text-xs font-semibold uppercase tracking-wider flex items-center gap-1.5', isDark ? 'text-slate-300' : 'text-slate-700')}>
                  <Layers className="w-3.5 h-3.5 text-sky-500" />
                  Qualifier Alignment Matrix
                </span>
                <span className={cn('text-[11px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
                  Comparability Gate Evaluation
                </span>
              </div>
              <QualifierDiffTable
                factA={relation.source_fact}
                factB={relation.target_fact}
                qualifierDiff={relation.qualifier_diff}
              />
            </div>
          )}
        </div>
      ) : null}
    </Modal>
  );
};
