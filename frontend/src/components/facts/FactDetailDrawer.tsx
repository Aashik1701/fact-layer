import React, { useEffect, useState } from 'react';
import { Modal } from '@/components/common/Modal';
import { FactSummary, FactFull, RelationSummary } from '@/types';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { GateVerdictBadge } from '@/components/relations/GateVerdictBadge';
import { RetrievalCandidatesPanel } from '@/components/facts/RetrievalCandidatesPanel';
import { KnowledgeGraphModal } from '@/components/graph/KnowledgeGraphModal';
import { LineagePanel } from '@/components/lineage/LineagePanel';
import { HistoryTimeline } from '@/components/history/HistoryTimeline';
import { fetchFact, fetchRelations } from '@/lib/api';
import { deriveSpanVerification, deriveValueVerification, deriveContextVerification } from '@/lib/verification';
import {
  FileSearch, CheckCircle2, AlertCircle, HelpCircle, GitCompare, ArrowRight, Loader2, Network,
} from 'lucide-react';
import { formatIssuer } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface FactDetailDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  fact: FactSummary | null;
  onOpenEvidence: (fact: FactSummary) => void;
  onOpenRelation?: (relationId: string) => void;
}

// A small checklist row for the VERIFICATION section. Three distinct states,
// never collapsed into a single boolean: confirmed (backend said so),
// contradicted (backend said so), and "not evaluated" / "not applicable" -
// which must never be rendered as a green success state (Phase 9).
const VerificationRow: React.FC<{
  label: string;
  state: 'confirmed' | 'failed' | 'unknown';
  detail?: string;
  isDark: boolean;
}> = ({ label, state, detail, isDark }) => {
  const icon =
    state === 'confirmed' ? (
      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
    ) : state === 'failed' ? (
      <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
    ) : (
      <HelpCircle className={cn('w-3.5 h-3.5 shrink-0', isDark ? 'text-slate-500' : 'text-slate-400')} />
    );
  return (
    <div className="flex items-start gap-2 py-1.5">
      {icon}
      <div className="min-w-0">
        <span
          className={cn(
            'text-xs font-mono font-medium',
            state === 'confirmed'
              ? 'text-emerald-500'
              : state === 'failed'
                ? 'text-amber-500'
                : isDark ? 'text-slate-400' : 'text-slate-500'
          )}
        >
          {label}
        </span>
        {detail && (
          <p className={cn('text-[11px] leading-relaxed mt-0.5', isDark ? 'text-slate-400' : 'text-slate-500')}>
            {detail}
          </p>
        )}
      </div>
    </div>
  );
};

export const FactDetailDrawer: React.FC<FactDetailDrawerProps> = ({
  isOpen,
  onClose,
  fact,
  onOpenEvidence,
  onOpenRelation,
}) => {
  const { isDark } = useTheme();

  const [fullFact, setFullFact] = useState<FactFull | null>(null);
  const [loadingFact, setLoadingFact] = useState<boolean>(false);

  const [relations, setRelations] = useState<RelationSummary[] | null>(null);
  const [loadingRelations, setLoadingRelations] = useState<boolean>(false);
  const [relationsError, setRelationsError] = useState<boolean>(false);
  const [graphOpen, setGraphOpen] = useState<boolean>(false);

  useEffect(() => {
    if (!isOpen || !fact) {
      setFullFact(null);
      setRelations(null);
      setRelationsError(false);
      return;
    }

    setLoadingFact(true);
    fetchFact(fact.fact_id)
      .then((data) => setFullFact(data))
      .catch((err) => console.error('Failed to load full fact:', err))
      .finally(() => setLoadingFact(false));

    setLoadingRelations(true);
    setRelationsError(false);
    fetchRelations({ fact_id: fact.fact_id })
      .then((res) => setRelations(res.relations))
      .catch((err) => {
        console.error('Failed to load relations for fact:', err);
        setRelationsError(true);
      })
      .finally(() => setLoadingRelations(false));
  }, [isOpen, fact?.fact_id]);

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

  const primaryEvidence = fullFact?.evidence?.[0] ?? null;

  const span = deriveSpanVerification(primaryEvidence, loadingFact);
  const value = deriveValueVerification(fact);
  const context = deriveContextVerification(fact);

  const hasSourceContext = !!(
    primaryEvidence &&
    (primaryEvidence.row_label || primaryEvidence.column_header || primaryEvidence.unit_context)
  );

  const hasBbox = !!primaryEvidence?.bbox;

  const periodDisplay =
    fact.qualifiers.period?.label ||
    (fact.qualifiers.period?.start || fact.qualifiers.period?.end
      ? `${fact.qualifiers.period?.start || ''} - ${fact.qualifiers.period?.end || ''}`.trim()
      : null);

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
      subtitle="Fact → Evidence → Verification → Comparison"
      maxWidth="3xl"
    >
      <div className="space-y-6 text-xs">
        {/* FACT - headline value */}
        <div className={cn(cardCls, 'text-center py-6')}>
          <span className={labelCls}>Canonical Value</span>
          <div className="text-3xl font-mono font-bold text-emerald-500 mt-1">
            {fact.value.normalized}{' '}
            <span className={cn('text-base font-normal', isDark ? 'text-slate-400' : 'text-slate-500')}>
              {fact.value.unit || ''}
            </span>
          </div>
          <div className={cn('text-xs font-mono mt-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
            "{fact.value.raw}"
          </div>
        </div>

        {/* Structured metadata */}
        <div
          className={cn(
            'p-4 rounded-xl border grid grid-cols-2 sm:grid-cols-3 gap-4',
            isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <div>
            <span className={labelCls}>Period</span>
            <span className={valueCls}>{periodDisplay || 'Not stated'}</span>
          </div>
          <div>
            <span className={labelCls}>Scope</span>
            <span className={valueCls}>{fact.qualifiers.scope || 'unknown'}</span>
          </div>
          <div>
            <span className={labelCls}>Issuer</span>
            <span className={cn(valueCls, 'font-sans font-medium')}>{formatIssuer(fact.qualifiers.issuer)}</span>
          </div>
          <div>
            <span className={labelCls}>Basis</span>
            <span className={valueCls}>{fact.qualifiers.basis || 'not stated'}</span>
          </div>
          <div>
            <span className={labelCls}>Modality</span>
            <span className={valueCls}>{fact.modality}</span>
          </div>
          <div>
            <span className={labelCls}>Confidence</span>
            <div className="mt-0.5"><ConfidencePill confidence={fact.confidence} showIcon /></div>
          </div>
          <div className="col-span-2 sm:col-span-3">
            <span className={labelCls}>Source</span>
            <span className={cn(valueCls, 'truncate block')}>{fact.doc_id || 'Unknown document'} · Page {fact.page ?? 1}</span>
          </div>
        </div>

        {/* VERIFICATION */}
        <div
          className={cn(
            'p-4 rounded-xl border',
            isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <span
            className={cn(
              'text-[11px] font-semibold uppercase tracking-wider block border-b pb-2 mb-1',
              isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
            )}
          >
            Verification
          </span>
          <VerificationRow label={span.label} state={span.state} detail={span.detail} isDark={isDark} />
          <VerificationRow label={value.label} state={value.state} detail={value.detail} isDark={isDark} />
          <VerificationRow label={context.label} state={context.state} detail={context.detail} isDark={isDark} />
        </div>

        {/* SOURCE CONTEXT */}
        {hasSourceContext && (
          <div
            className={cn(
              'p-4 rounded-xl border',
              isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            )}
          >
            <span
              className={cn(
                'text-[11px] font-semibold uppercase tracking-wider block border-b pb-2 mb-2',
                isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
              )}
            >
              Source Context
            </span>
            <div className="grid grid-cols-2 gap-3">
              {primaryEvidence?.row_label && (
                <div>
                  <span className={labelCls}>Row</span>
                  <span className={valueCls}>{primaryEvidence.row_label}</span>
                </div>
              )}
              {primaryEvidence?.column_header && (
                <div>
                  <span className={labelCls}>Column</span>
                  <span className={valueCls}>{primaryEvidence.column_header}</span>
                </div>
              )}
              {primaryEvidence?.unit_context && (
                <div className="col-span-2">
                  <span className={labelCls}>Unit</span>
                  <span className={valueCls}>{primaryEvidence.unit_context}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* EVIDENCE ACTION */}
        <div
          className={cn(
            'p-4 rounded-xl border flex items-center justify-between gap-4',
            isDark ? 'bg-slate-900 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <div className="space-y-0.5 min-w-0">
            <span className={labelCls}>PDF Provenance</span>
            <div className={cn('font-mono text-xs truncate max-w-sm', isDark ? 'text-slate-200' : 'text-slate-700')}>
              {fact.doc_id || 'Unknown Document'}
            </div>
            <span className={cn('text-[11px]', isDark ? 'text-slate-400' : 'text-slate-500')}>
              {!loadingFact && !hasBbox
                ? 'Page-level evidence only - no exact bounding box recorded for this span.'
                : `PDF Page ${fact.page ?? 1}`}
            </span>
          </div>

          <button
            onClick={() => { onClose(); onOpenEvidence(fact); }}
            className="px-4 py-2.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-white font-bold font-mono text-xs flex items-center gap-1.5 shadow-md shadow-sky-500/20 transition-all shrink-0"
          >
            <FileSearch className="w-4 h-4" />
            Inspect Evidence
          </button>
        </div>

        {/* TEMPORAL KNOWLEDGE - the full history of this subject::measure,
            chronologically ordered and grouped by scope/modality, with any
            adjudicated relations between points surfaced as-is (see
            fact_layer/temporal.py). Never interpolated. */}
        {fact.subject && (
          <HistoryTimeline
            subject={fact.subject}
            measure={fact.measure}
            onOpenEvidence={onOpenEvidence}
          />
        )}

        {/* EVIDENCE LINEAGE - the provenance chain this fact rests on
            (fact -> evidence -> page -> document). A read-only projection,
            never invented confidence or relationships. */}
        <LineagePanel
          factId={fact.fact_id}
          onOpenEvidence={onOpenEvidence}
        />

        {/* COMPARE THIS FACT */}
        <div
          className={cn(
            'p-4 rounded-xl border',
            isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
          )}
        >
          <span
            className={cn(
              'text-[11px] font-semibold uppercase tracking-wider flex items-center gap-1.5 border-b pb-2 mb-2',
              isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
            )}
          >
            <GitCompare className="w-3.5 h-3.5 text-sky-500" />
            Compare This Fact
          </span>

          {loadingRelations ? (
            <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Checking the comparability gate against related facts…
            </div>
          ) : relationsError ? (
            <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')}>
              Unable to load comparisons for this fact right now.
            </p>
          ) : relations && relations.length > 0 ? (
            <div className="space-y-2">
              {relations.map((rel) => {
                const otherSide = rel.source_fact_id === fact.fact_id ? rel.target_summary : rel.source_summary;
                return (
                  <button
                    key={rel.relation_id}
                    onClick={() => onOpenRelation && onOpenRelation(rel.relation_id)}
                    disabled={!onOpenRelation}
                    className={cn(
                      'w-full flex items-center justify-between gap-3 p-2.5 rounded-lg border text-left transition-colors',
                      isDark
                        ? 'bg-slate-950/40 border-slate-800 hover:bg-slate-800/60'
                        : 'bg-white border-slate-200 hover:bg-slate-100',
                      !onOpenRelation && 'cursor-default'
                    )}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <GateVerdictBadge verdict={rel.relation} size="sm" />
                      <span className={cn('font-mono text-[11px] truncate', isDark ? 'text-slate-300' : 'text-slate-600')}>
                        <ArrowRight className="w-3 h-3 inline mx-1 text-slate-400" />
                        {otherSide || 'related fact'}
                      </span>
                    </div>
                    <ConfidencePill confidence={rel.confidence} />
                  </button>
                );
              })}
            </div>
          ) : (
            <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')}>
              No relations have been formed for this fact yet - either no comparable
              fact exists in the corpus, or nothing has been evaluated against it.
            </p>
          )}
        </div>

        {/* CANDIDATE RETRIEVAL - section 19: shows what retrieval found,
            never what it decided; the comparability gate above remains
            the sole authority for CORROBORATES/CONTRADICTS/etc. */}
        {/* Graph entry point (spec §13B): start a provenance walk from
            this fact - entity, evidence, document and any recorded
            relationships, one hop at a time. */}
        <button
          type="button"
          onClick={() => setGraphOpen(true)}
          className={cn(
            'w-full px-3 py-2 rounded-xl border text-[12px] font-medium flex items-center justify-center gap-1.5',
            isDark
              ? 'bg-slate-900/60 border-slate-800 text-slate-300 hover:border-sky-500/50'
              : 'bg-slate-50 border-slate-200 text-slate-600 hover:border-sky-500/50'
          )}
        >
          <Network className="w-3.5 h-3.5 text-sky-500" aria-hidden="true" />
          Explore in knowledge graph
        </button>

        <RetrievalCandidatesPanel fact={fact} />

        <KnowledgeGraphModal
          isOpen={graphOpen}
          onClose={() => setGraphOpen(false)}
          rootType="fact"
          rootId={fact.fact_id}
          title={`${fact.subject} · ${fact.measure}`}
          onOpenEvidence={onOpenEvidence}
        />
      </div>
    </Modal>
  );
};
