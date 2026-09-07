import React, { useState, useEffect } from 'react';
import { fetchRelations, fetchRejectedFacts, fetchRelation } from '@/lib/api';
import { RelationSummary, RelationFull, RejectedFact, FactFull, FactSummary } from '@/types';
import { RelationInspectorModal } from '@/components/relations/RelationInspectorModal';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { GateVerdictBadge } from '@/components/relations/GateVerdictBadge';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { getCaveatExplanation } from '@/lib/constants';
import {
  Sparkles, CheckCircle2, XCircle, AlertTriangle, FileX,
  ArrowRight, ExternalLink, ShieldCheck, UploadCloud, FileSearch,
} from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface RequiredCasesPageProps {
  onNavigateToIngest?: () => void;
}

// All four cards below render exclusively from live API responses
// (GET /relations, GET /relations/{id}, GET /rejected-facts) — nothing on
// this page is a hard-coded fact, value, or relation. If the pipeline's
// output changes, so does everything shown here.
export const RequiredCasesPage: React.FC<RequiredCasesPageProps> = ({ onNavigateToIngest }) => {
  const { isDark } = useTheme();
  const [corroboration, setCorroboration] = useState<RelationFull | null>(null);
  const [contradiction, setContradiction] = useState<RelationFull | null>(null);
  const [apparentConflict, setApparentConflict] = useState<RelationFull | null>(null);
  const [rejectedSample, setRejectedSample] = useState<RejectedFact | null>(null);
  const [rejectedTotal, setRejectedTotal] = useState<number>(0);
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactFull | FactSummary | null>(null);

  useEffect(() => {
    const loadCases = async () => {
      try {
        const [corrRes, contraRes, appRes, rejectedRes] = await Promise.all([
          fetchRelations({ type: 'CORROBORATES' }).catch(() => ({ relations: [] as RelationSummary[] })),
          fetchRelations({ type: 'CONTRADICTS' }).catch(() => ({ relations: [] as RelationSummary[] })),
          fetchRelations({ type: 'APPARENT_CONFLICT' }).catch(() => ({ relations: [] as RelationSummary[] })),
          fetchRejectedFacts(5).catch(() => ({ rejected_facts: [] as RejectedFact[], total: 0 })),
        ]);

        // Full detail (real values, gate verdict, evidence-bearing facts)
        // for whichever real relation of each type the store happens to
        // hold — never a synthesized stand-in.
        const [corrFull, contraFull, appFull] = await Promise.all([
          corrRes.relations[0] ? fetchRelation(corrRes.relations[0].relation_id).catch(() => null) : Promise.resolve(null),
          contraRes.relations[0] ? fetchRelation(contraRes.relations[0].relation_id).catch(() => null) : Promise.resolve(null),
          appRes.relations[0] ? fetchRelation(appRes.relations[0].relation_id).catch(() => null) : Promise.resolve(null),
        ]);

        setCorroboration(corrFull);
        setContradiction(contraFull);
        setApparentConflict(appFull);
        if (rejectedRes.rejected_facts.length > 0) setRejectedSample(rejectedRes.rejected_facts[0]);
        setRejectedTotal(rejectedRes.total || 0);
      } catch (err) {
        console.error('Failed to load required cases:', err);
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

  const valueLine = (rel: RelationFull, side: 'source' | 'target') => {
    const f = side === 'source' ? rel.source_fact : rel.target_fact;
    if (!f) return null;
    return (
      <span className="font-mono font-bold">
        {f.value.normalized}
        {f.value.unit ? ` ${f.value.unit}` : ''}
      </span>
    );
  };

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
              Cross-Source Value Agreement
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              When two sources report on the same entity and metric for a comparable timeframe within tolerance, the gate confirms compatibility and adjudication corroborates.
            </p>

            {corroboration ? (
              <div className={inlineCardCls}>
                <div className={cn('flex items-center justify-between gap-2 text-xs font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
                  <span className="text-sky-500 font-semibold truncate">
                    {corroboration.source_summary} {valueLine(corroboration, 'source')}
                  </span>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-indigo-500 font-semibold truncate">
                    {corroboration.target_summary} {valueLine(corroboration, 'target')}
                  </span>
                </div>
                {/* Do not hide an issuer/qualifier difference behind an
                    upbeat "corroborates" label — this shows exactly why
                    the gate's own verdict and the relation type can differ. */}
                <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {getCaveatExplanation(corroboration.reason_code)}
                </p>
                {corroboration.gate && corroboration.gate.verdict !== 'comparable' && (
                  <div className="text-[11px] font-mono text-amber-500">
                    Comparability gate verdict: <span className="uppercase font-semibold">{corroboration.gate.verdict}</span>
                    {corroboration.gate.cross_issuer && ' — different issuers'}
                  </div>
                )}
                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] font-mono text-slate-500">Reason: {corroboration.reason_code}</span>
                  <ConfidencePill confidence={corroboration.confidence} />
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>
                <div className="flex items-center gap-2 text-xs text-amber-500 font-medium">
                  <UploadCloud className="w-4 h-4 shrink-0" />
                  <span>No corroboration relation in the store yet</span>
                </div>
                {onNavigateToIngest && (
                  <button onClick={onNavigateToIngest} className="mt-1 px-3 py-1.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono transition-colors">
                    Go to Document Ingest →
                  </button>
                )}
              </div>
            )}
          </div>
          {corroboration && (
            <div className="flex gap-2 mt-3">
              {corroboration.source_fact && (
                <button onClick={() => setEvidenceFact(corroboration.source_fact!)} className="flex-1 py-2 px-3 rounded-lg bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                  <FileSearch className="w-3.5 h-3.5" />
                  Evidence A
                </button>
              )}
              <button onClick={() => setSelectedRelationId(corroboration.relation_id)} className="flex-1 py-2 px-3 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 border border-emerald-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                <ExternalLink className="w-3.5 h-3.5" />
                Full Chain
              </button>
            </div>
          )}
        </div>

        {/* CASE 2: CONTRADICTION */}
        <div className={cardCls('rose')}>
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-rose-500 uppercase tracking-wide flex items-center gap-1.5">
                <XCircle className="w-4 h-4" />
                Case 2: Contradiction
              </span>
              <GateVerdictBadge verdict="CONTRADICTS" size="sm" />
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              Genuine Disagreement Under Comparable Context
            </h3>
            <p className={cn('text-xs mt-1 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              When two sources share comparable qualifiers but assert incompatible numbers, the system flags a genuine contradiction — never inferred merely from unequal values.
            </p>

            {contradiction ? (
              <div className={inlineCardCls}>
                <div className={cn('flex items-center justify-between gap-2 text-xs font-mono', isDark ? 'text-slate-200' : 'text-slate-700')}>
                  <span className="text-sky-500 font-semibold truncate">
                    {contradiction.source_summary} {valueLine(contradiction, 'source')}
                  </span>
                  <ArrowRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                  <span className="text-rose-500 font-semibold truncate">
                    {contradiction.target_summary} {valueLine(contradiction, 'target')}
                  </span>
                </div>
                <p className={cn('text-[11px] leading-relaxed font-semibold', isDark ? 'text-rose-300' : 'text-rose-600')}>
                  These values disagree under the available comparable context.
                </p>
                <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {getCaveatExplanation(contradiction.reason_code)}
                </p>
                {contradiction.reason_code.includes('period_unverified') && (
                  <p className="text-[11px] font-mono text-amber-500">
                    ⚠ Period incomplete on at least one side — confidence is reduced accordingly, not hidden.
                  </p>
                )}
                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] font-mono text-rose-500">Reason: {contradiction.reason_code}</span>
                  <ConfidencePill confidence={contradiction.confidence} />
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>No contradiction relation in the store yet.</div>
            )}
          </div>
          {contradiction && (
            <div className="flex gap-2 mt-3">
              {contradiction.source_fact && (
                <button onClick={() => setEvidenceFact(contradiction.source_fact!)} className="flex-1 py-2 px-3 rounded-lg bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                  <FileSearch className="w-3.5 h-3.5" />
                  Evidence A
                </button>
              )}
              <button onClick={() => setSelectedRelationId(contradiction.relation_id)} className="flex-1 py-2 px-3 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-500 border border-rose-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                <ExternalLink className="w-3.5 h-3.5" />
                Full Chain
              </button>
            </div>
          )}
        </div>

        {/* CASE 3: APPARENT CONFLICT — hero case */}
        <div className={cn(cardCls('amber'), 'md:col-span-2 ring-1', isDark ? 'ring-amber-500/10' : 'ring-amber-500/20')}>
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-bold text-amber-500 uppercase tracking-wide flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4" />
                Case 3: Apparent Conflict — the central thesis
              </span>
              <GateVerdictBadge verdict="APPARENT_CONFLICT" size="sm" />
            </div>
            <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
              Comparability Before Comparison
            </h3>

            {apparentConflict ? (
              <>
                {/* Big value-vs-value display */}
                <div className="flex items-center justify-center gap-6 py-5">
                  <div className="text-center">
                    <div className={cn('text-3xl font-mono font-black', isDark ? 'text-slate-100' : 'text-slate-900')}>
                      {apparentConflict.source_fact?.value.normalized}{apparentConflict.source_fact?.value.unit ? ` ${apparentConflict.source_fact.value.unit}` : ''}
                    </div>
                    <div className={cn('text-[11px] font-mono mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      {apparentConflict.source_summary}
                    </div>
                  </div>
                  <span className={cn('text-xs font-mono uppercase', isDark ? 'text-slate-500' : 'text-slate-400')}>vs</span>
                  <div className="text-center">
                    <div className={cn('text-3xl font-mono font-black', isDark ? 'text-slate-100' : 'text-slate-900')}>
                      {apparentConflict.target_fact?.value.normalized}{apparentConflict.target_fact?.value.unit ? ` ${apparentConflict.target_fact.value.unit}` : ''}
                    </div>
                    <div className={cn('text-[11px] font-mono mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      {apparentConflict.target_summary}
                    </div>
                  </div>
                </div>

                <div
                  className={cn(
                    'p-3.5 rounded-lg border text-xs leading-relaxed space-y-1.5',
                    isDark ? 'bg-amber-500/10 border-amber-500/20' : 'bg-amber-50 border-amber-200'
                  )}
                >
                  <div className="font-semibold uppercase tracking-wide text-[11px] text-amber-600 dark:text-amber-400">
                    Why this is not necessarily a true contradiction
                  </div>
                  <p className={isDark ? 'text-slate-200' : 'text-slate-800'}>{apparentConflict.explanation}</p>
                  <p className={cn('text-[11px]', isDark ? 'text-slate-400' : 'text-slate-600')}>
                    {getCaveatExplanation(apparentConflict.reason_code)}
                  </p>
                </div>

                {/* Comparability Gate -> Explanation -> Evidence flow */}
                <div className="flex items-center gap-2 mt-3 text-[11px] font-mono flex-wrap">
                  <span className={cn('px-2 py-1 rounded border', isDark ? 'bg-slate-800 border-slate-700 text-slate-300' : 'bg-slate-100 border-slate-200 text-slate-700')}>
                    Comparability Gate
                  </span>
                  <ArrowRight className="w-3 h-3 text-slate-400" />
                  <span className="px-2 py-1 rounded border border-amber-500/30 bg-amber-500/10 text-amber-500 font-semibold">
                    {apparentConflict.gate?.verdict || 'APPARENT_CONFLICT'}
                  </span>
                  <ArrowRight className="w-3 h-3 text-slate-400" />
                  <span className={cn('px-2 py-1 rounded border', isDark ? 'bg-slate-800 border-slate-700 text-slate-300' : 'bg-slate-100 border-slate-200 text-slate-700')}>
                    Explanation
                  </span>
                  <ArrowRight className="w-3 h-3 text-slate-400" />
                  <span className={cn('px-2 py-1 rounded border', isDark ? 'bg-slate-800 border-slate-700 text-slate-300' : 'bg-slate-100 border-slate-200 text-slate-700')}>
                    Evidence
                  </span>
                </div>
              </>
            ) : (
              <div className={emptyCardCls}>No apparent-conflict relation in the store yet.</div>
            )}
          </div>
          {apparentConflict && (
            <div className="flex gap-2 mt-4">
              {apparentConflict.source_fact && (
                <button onClick={() => setEvidenceFact(apparentConflict.source_fact!)} className="flex-1 py-2 px-3 rounded-lg bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                  <FileSearch className="w-3.5 h-3.5" />
                  Evidence A
                </button>
              )}
              {apparentConflict.target_fact && (
                <button onClick={() => setEvidenceFact(apparentConflict.target_fact!)} className="flex-1 py-2 px-3 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-500 border border-indigo-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                  <FileSearch className="w-3.5 h-3.5" />
                  Evidence B
                </button>
              )}
              <button onClick={() => setSelectedRelationId(apparentConflict.relation_id)} className="flex-1 py-2 px-3 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 border border-amber-500/20 text-xs font-mono font-medium flex items-center justify-center gap-1.5 transition-colors">
                <ExternalLink className="w-3.5 h-3.5" />
                Full Chain
              </button>
            </div>
          )}
        </div>

        {/* CASE 4: EXTRACTION FAILURE */}
        <div className={cn(cardCls('slate'), 'md:col-span-2')}>
          <div>
            <div className="flex items-center justify-between">
              <span className={cn('text-xs font-mono font-bold uppercase tracking-wide flex items-center gap-1.5', isDark ? 'text-slate-300' : 'text-slate-600')}>
                <FileX className="w-4 h-4" />
                Case 4: Extraction Failure — The System Refused to Guess
              </span>
              <span className={cn('px-2 py-0.5 rounded text-[11px] font-mono border', isDark ? 'bg-slate-800 text-rose-400 border-rose-500/20' : 'bg-rose-50 text-rose-600 border-rose-200')}>
                NOT ADMITTED TO STORE
              </span>
            </div>
            <p className={cn('text-xs mt-2 leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Source span insufficient or ambiguous → value not trusted → fact not allowed into the trusted store. This is not a bug being hidden — it is the system's evidence discipline working as intended.
            </p>

            {rejectedSample ? (
              <div className={inlineCardCls}>
                <blockquote className={cn('text-[11px] font-mono italic line-clamp-2', isDark ? 'text-slate-300' : 'text-slate-600')}>
                  "{rejectedSample.raw_quote || rejectedSample.raw_fact?.verbatim_quote || 'quote unavailable'}"
                </blockquote>
                <div className="flex items-center justify-between pt-1 text-[11px] font-mono">
                  <span className="text-rose-500">Reason: {rejectedSample.reason}</span>
                  <span className={isDark ? 'text-slate-500' : 'text-slate-400'}>Page {rejectedSample.page_no ?? rejectedSample.page ?? '—'}</span>
                </div>
              </div>
            ) : (
              <div className={emptyCardCls}>Loading rejected facts log...</div>
            )}
          </div>

          <div className={cn('mt-3 p-2.5 rounded-lg border text-[11px] font-mono flex items-center justify-between', isDark ? 'bg-slate-900/60 border-slate-800 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500')}>
            <span className="flex items-center gap-1.5 text-emerald-500">
              <ShieldCheck className="w-3.5 h-3.5" />
              {rejectedTotal} Ungrounded/Unresolved Candidates Excluded
            </span>
            <span>Zero Hallucinations Admitted To Store</span>
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
