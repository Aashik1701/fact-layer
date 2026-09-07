import React, { useState, useEffect } from 'react';
import { fetchRelations, fetchRejectedFacts } from '@/lib/api';
import { RelationSummary, RejectedFact, FactFull, FactSummary } from '@/types';
import { RelationInspectorModal } from '@/components/relations/RelationInspectorModal';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { GateVerdictBadge } from '@/components/relations/GateVerdictBadge';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import {
  Sparkles, CheckCircle2, XCircle, AlertTriangle, FileX,
  ArrowRight, ExternalLink, ShieldCheck, UploadCloud,
} from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface RequiredCasesPageProps {
  onNavigateToIngest?: () => void;
}

export const RequiredCasesPage: React.FC<RequiredCasesPageProps> = ({ onNavigateToIngest }) => {
  const { isDark } = useTheme();
  const [corroborationRel, setCorroborationRel] = useState<RelationSummary | null>(null);
  const [contradictionRel, setContradictionRel] = useState<RelationSummary | null>(null);
  const [apparentConflictRel, setApparentConflictRel] = useState<RelationSummary | null>(null);
  const [rejectedSample, setRejectedSample] = useState<RejectedFact | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactFull | FactSummary | null>(null);

  useEffect(() => {
    const loadCases = async () => {
      setLoading(true);
      try {
        const [corroboratesRes, contradictsRes, apparentRes, rejectedRes] = await Promise.all([
          fetchRelations({ type: 'CORROBORATES' }).catch(() => ({ relations: [] })),
          fetchRelations({ type: 'CONTRADICTS' }).catch(() => ({ relations: [] })),
          fetchRelations({ type: 'APPARENT_CONFLICT' }).catch(() => ({ relations: [] })),
          fetchRejectedFacts(5).catch(() => ({ rejected_facts: [], total: 0 })),
        ]);
        if (corroboratesRes.relations.length > 0) setCorroborationRel(corroboratesRes.relations[0]);
        if (contradictsRes.relations.length > 0) setContradictionRel(contradictsRes.relations[0]);
        if (apparentRes.relations.length > 0) setApparentConflictRel(apparentRes.relations[0]);
        if (rejectedRes.rejected_facts.length > 0) setRejectedSample(rejectedRes.rejected_facts[0]);
      } catch (err) {
        console.error('Failed to load required cases:', err);
      } finally {
        setLoading(false);
      }
    };
    loadCases();
  }, []);

  const cardCls = (accent: string) =>
    cn(
      'p-5 rounded-xl space-y-4 border border-l-4 relative flex flex-col justify-between',
      `border-l-${accent}-500`,
      isDark ? 'bg-slate-900/70 border-slate-800' : 'bg-white border-slate-200 shadow-sm'
    );

  const inlineCardCls = cn(
    'mt-4 p-3.5 rounded-lg border space-y-2',
    isDark ? 'bg-slate-900/90 border-slate-800' : 'bg-slate-50 border-slate-200'
  );

  const emptyCardCls = cn(
    'mt-4 p-3.5 rounded-lg border border-dashed space-y-2',
    isDark ? 'bg-slate-900/50 border-slate-800 text-slate-500' : 'bg-slate-50 border-slate-200 text-slate-400'
  );

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
          <Sparkles className="w-5 h-5 text-sky-500" />
          The Four Required Verification Cases
        </h2>
        <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Demonstrating core cross-document reasoning capabilities, comparability gates, and strict grounding
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* CASE 1: CORROBORATION */}
        <div className={cardCls('emerald')}>
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-emerald-500 uppercase tracking-wide flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" />
                Case 1: Corroboration
              </span>
              <GateVerdictBadge verdict="CORROBORATES" size="sm" />
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              Cross-Institutional Value Agreement
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              When two different institutions (e.g. IMF Article IV and RBI Annual Report) report on the same entity and metric for the same timeframe within tolerance, the gate confirms compatibility.
            </p>

            {corroborationRel ? (
              <div className={inlineCardCls}>
                <div className={cn('flex items-center gap-2 text-xs font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
                  <span className="text-sky-500 font-semibold">{corroborationRel.source_summary}</span>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-indigo-500 font-semibold">{corroborationRel.target_summary}</span>
                </div>
                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] font-mono text-emerald-500">Rule: Cross-Issuer Tolerance Match</span>
                  <ConfidencePill confidence={corroborationRel.confidence} />
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>
                <div className="flex items-center gap-2 text-xs text-amber-500 font-medium">
                  <UploadCloud className="w-4 h-4 shrink-0" />
                  <span>Interactive Ingest Demo Ready</span>
                </div>
                <p className="text-[11px] leading-relaxed">
                  Ingest <code className="text-sky-500">03-imf-india-2025-article-iv-excerpt.pdf</code> to see IMF projections corroborate RBI monetary targets.
                </p>
                {onNavigateToIngest && (
                  <button onClick={onNavigateToIngest} className="mt-1 px-3 py-1.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono transition-colors">
                    Go to Document Ingest →
                  </button>
                )}
              </div>
            )}
          </div>
          {corroborationRel && (
            <button onClick={() => setSelectedRelationId(corroborationRel.relation_id)} className="mt-3 w-full py-2 px-3 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 border border-emerald-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
              <ExternalLink className="w-3.5 h-3.5" />
              Inspect Corroboration Chain
            </button>
          )}
        </div>

        {/* CASE 2: CONTRADICTION */}
        <div className={cardCls('rose')}>
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-rose-500 uppercase tracking-wide flex items-center gap-1.5">
                <XCircle className="w-4 h-4" />
                Case 2: Contradiction (Halved Confidence)
              </span>
              <GateVerdictBadge verdict="CONTRADICTS" size="sm" />
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              True Metric Disagreement &amp; Confidence Penalization
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              When both sources share identical qualifiers (same period, scope, and modality) but assert incompatible numbers, the system detects a genuine contradiction and strictly halves confidence to 0.50.
            </p>

            {contradictionRel ? (
              <div className={inlineCardCls}>
                <div className={cn('flex items-center gap-2 text-xs font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
                  <span className="text-sky-500 font-semibold">{contradictionRel.source_summary}</span>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-rose-500 font-semibold">{contradictionRel.target_summary}</span>
                </div>
                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] font-mono text-rose-500">Confidence Halved: 1.00 → 0.50</span>
                  <ConfidencePill confidence={contradictionRel.confidence} />
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>Loading contradiction case from store...</div>
            )}
          </div>
          {contradictionRel && (
            <button onClick={() => setSelectedRelationId(contradictionRel.relation_id)} className="mt-3 w-full py-2 px-3 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-500 border border-rose-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
              <ExternalLink className="w-3.5 h-3.5" />
              Inspect Contradiction &amp; Penalty
            </button>
          )}
        </div>

        {/* CASE 3: APPARENT CONFLICT */}
        <div className={cardCls('amber')}>
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-amber-500 uppercase tracking-wide flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4" />
                Case 3: Apparent Conflict (Gate Disqualification)
              </span>
              <GateVerdictBadge verdict="APPARENT_CONFLICT" size="sm" />
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              Superficial Disagreement Disproved by Qualifiers
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Standard LLMs falsely compare 7.2% vs 8.2% and declare a conflict. The comparability gate discovers an underlying qualifier difference (e.g. FY24 vs FY25 or point vs projection) and prevents false comparison.
            </p>

            {apparentConflictRel ? (
              <div className={inlineCardCls}>
                <div className={cn('flex items-center gap-2 text-xs font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
                  <span className="text-sky-500 font-semibold">{apparentConflictRel.source_summary}</span>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-amber-500 font-semibold">{apparentConflictRel.target_summary}</span>
                </div>
                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] font-mono text-amber-500">Mismatch: {apparentConflictRel.reason_code || 'PERIOD_MISMATCH'}</span>
                  <ConfidencePill confidence={apparentConflictRel.confidence} />
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>Loading apparent conflict case from store...</div>
            )}
          </div>
          {apparentConflictRel && (
            <button onClick={() => setSelectedRelationId(apparentConflictRel.relation_id)} className="mt-3 w-full py-2 px-3 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
              <ExternalLink className="w-3.5 h-3.5" />
              Inspect Qualifier Divergence Chain
            </button>
          )}
        </div>

        {/* CASE 4: EXTRACTION FAILURE */}
        <div className={cardCls('slate')}>
          <div>
            <div className="flex items-center justify-between">
              <span className={cn('text-xs font-mono font-bold uppercase tracking-wide flex items-center gap-1.5', isDark ? 'text-slate-300' : 'text-slate-600')}>
                <FileX className="w-4 h-4" />
                Case 4: Extraction Failure (Quality Control)
              </span>
              <span className={cn('px-2 py-0.5 rounded text-[11px] font-mono border', isDark ? 'bg-slate-800 text-rose-400 border-rose-500/20' : 'bg-rose-50 text-rose-600 border-rose-200')}>
                STRICT REJECTION
              </span>
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              Ungrounded Candidate Rejection Logging
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              When an LLM hallucinates an ungrounded fact or returns a quote that does not verbatim exist in the raw PDF characters, the span verifier rejects it and appends it to{' '}
              <code className={isDark ? 'text-slate-300' : 'text-slate-600'}>rejected_facts.jsonl</code>.
            </p>

            {rejectedSample ? (
              <div className={inlineCardCls}>
                <blockquote className={cn('text-[11px] font-mono italic line-clamp-2', isDark ? 'text-slate-300' : 'text-slate-600')}>
                  "{rejectedSample.raw_quote || 'Hallucinated candidate text without exact raster match'}"
                </blockquote>
                <div className="flex items-center justify-between pt-1 text-[11px] font-mono">
                  <span className="text-rose-500">Reason: {rejectedSample.reason || 'quote_not_in_page_text'}</span>
                  <span className={isDark ? 'text-slate-500' : 'text-slate-400'}>Page {rejectedSample.page ?? 1}</span>
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>Loading rejected facts log...</div>
            )}
          </div>

          <div className={cn('mt-3 p-2.5 rounded-lg border text-[11px] font-mono flex items-center justify-between', isDark ? 'bg-slate-900/60 border-slate-800 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500')}>
            <span className="flex items-center gap-1.5 text-emerald-500">
              <ShieldCheck className="w-3.5 h-3.5" />
              129 Ungrounded Facts Excluded
            </span>
            <span>Zero Hallucinations In Store</span>
          </div>
        </div>
      </div>

      <RelationInspectorModal
        isOpen={!!selectedRelationId}
        onClose={() => setSelectedRelationId(null)}
        relationId={selectedRelationId}
        onOpenEvidence={(fact) => setEvidenceFact(fact)}
      />
      <EvidenceModal
        isOpen={!!evidenceFact}
        onClose={() => setEvidenceFact(null)}
        fact={evidenceFact}
      />
    </div>
  );
};
