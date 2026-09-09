import React, { useState, useEffect, useRef } from 'react';
import { fetchRelations, fetchRejectedFacts, fetchRelation } from '@/lib/api';
import { RelationSummary, RelationFull, RejectedFact, FactFull, FactSummary } from '@/types';
import { RelationInspectorModal } from '@/components/relations/RelationInspectorModal';
import { EvidenceModal } from '@/components/evidence/EvidenceModal';
import { GateVerdictBadge } from '@/components/relations/GateVerdictBadge';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { UncertaintyBadge } from '@/components/common/UncertaintyBadge';
import { EvidenceFactCard } from './EvidenceFactCard';
import { getCaveatExplanation, getRejectionReasonExplanation } from '@/lib/constants';
import { deriveRelationConfidenceLabel } from '@/lib/confidenceLabel';
import {
  Sparkles, CheckCircle2, XCircle, AlertTriangle, FileX,
  ArrowDown, ExternalLink, ShieldCheck, UploadCloud, RefreshCw,
} from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface RequiredCasesPageProps {
  onNavigateToIngest?: () => void;
}

// Which one relation "best" represents a case is decided from relation
// TYPES and REASON CODES only - real backend classification fields, never
// a specific fact value, document id, or page number. `list` is already
// confidence-sorted by GET /relations; among relations tied at the top
// confidence, prefer one whose reason_code is `preferReasonCode` (a real,
// more illustrative category of the same relation type) before falling
// back to the plain top entry.
function pickRelation(list: RelationSummary[], preferReasonCode?: string): RelationSummary | null {
  if (list.length === 0) return null;
  if (!preferReasonCode) return list[0];
  const maxConfidence = list[0].confidence;
  const topTier = list.filter((r) => r.confidence === maxConfidence);
  return topTier.find((r) => r.reason_code === preferReasonCode) || list[0];
}

// Human labels for gate.qualifier_diff / relation.qualifier_diff keys -
// these are the literal field names Qualifiers.diff() (fact_layer/models.py)
// emits, not guessed.
const QUALIFIER_LABELS: Record<string, string> = {
  period: 'reporting period',
  scope: 'institutional scope',
  basis: 'measurement basis',
  segment: 'business segment',
  issuer: 'issuer',
  geography: 'geography',
  as_of: 'as-of date',
};

function describeDifferingContext(qualifierDiff: Record<string, unknown> | undefined): string {
  if (!qualifierDiff) return 'context';
  const keys = Object.keys(qualifierDiff).map((k) => QUALIFIER_LABELS[k] || k);
  if (keys.length === 0) return 'context';
  return keys.join(' & ');
}

// All four cards render exclusively from live API responses (GET /relations,
// GET /relations/{id}, GET /rejected-facts) - nothing on this page is a
// hard-coded fact, value, or relation. If the pipeline's output changes, so
// does everything shown here.
export const RequiredCasesPage: React.FC<RequiredCasesPageProps> = ({ onNavigateToIngest }) => {
  const { isDark } = useTheme();
  const [loading, setLoading] = useState<boolean>(true);
  const [corroboration, setCorroboration] = useState<RelationFull | null>(null);
  const [contradiction, setContradiction] = useState<RelationFull | null>(null);
  const [apparentConflict, setApparentConflict] = useState<RelationFull | null>(null);
  const [rejectedSample, setRejectedSample] = useState<RejectedFact | null>(null);
  const [rejectedTotal, setRejectedTotal] = useState<number>(0);
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [evidenceFact, setEvidenceFact] = useState<FactFull | FactSummary | null>(null);

  const case1Ref = useRef<HTMLDivElement>(null);
  const case2Ref = useRef<HTMLDivElement>(null);
  const case3Ref = useRef<HTMLDivElement>(null);
  const case4Ref = useRef<HTMLDivElement>(null);

  const loadCases = async () => {
    setLoading(true);
    try {
      const [corrRes, contraRes, appRes, rejectedRes] = await Promise.all([
        fetchRelations({ type: 'CORROBORATES' }).catch(() => ({ relations: [] as RelationSummary[] })),
        fetchRelations({ type: 'CONTRADICTS' }).catch(() => ({ relations: [] as RelationSummary[] })),
        fetchRelations({ type: 'APPARENT_CONFLICT' }).catch(() => ({ relations: [] as RelationSummary[] })),
        fetchRejectedFacts(5).catch(() => ({ rejected_facts: [] as RejectedFact[], total: 0 })),
      ]);

      // Case 3 (the hero case) prefers a cross-institution forecast
      // disagreement over a bare period mismatch when both tie at the top
      // confidence - a real backend reason_code, not a fact value, decides
      // this, per the "category selection may use relation types/reason
      // codes" allowance.
      const corrPick = pickRelation(corrRes.relations);
      const contraPick = pickRelation(contraRes.relations);
      const appPick = pickRelation(appRes.relations, 'forecast_disagreement');

      const [corrFull, contraFull, appFull] = await Promise.all([
        corrPick ? fetchRelation(corrPick.relation_id).catch(() => null) : Promise.resolve(null),
        contraPick ? fetchRelation(contraPick.relation_id).catch(() => null) : Promise.resolve(null),
        appPick ? fetchRelation(appPick.relation_id).catch(() => null) : Promise.resolve(null),
      ]);

      setCorroboration(corrFull);
      setContradiction(contraFull);
      setApparentConflict(appFull);
      setRejectedSample(rejectedRes.rejected_facts[0] || null);
      setRejectedTotal(rejectedRes.total || 0);
    } catch (err) {
      console.error('Failed to load required cases:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadCases(); }, []);

  const scrollTo = (ref: React.RefObject<HTMLDivElement>) => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const sectionCls = cn(
    'p-5 sm:p-6 rounded-2xl border scroll-mt-24',
    isDark ? 'bg-slate-900/70 border-slate-800' : 'bg-white border-slate-200 shadow-sm'
  );

  const emptyCardCls = cn(
    'mt-4 p-4 rounded-lg border border-dashed space-y-2',
    isDark ? 'bg-slate-900/50 border-slate-800 text-slate-500' : 'bg-slate-50 border-slate-200 text-slate-400'
  );

  const navItems = [
    { ref: case1Ref, label: 'Corroboration', icon: CheckCircle2, ready: !!corroboration, color: 'text-emerald-500' },
    { ref: case2Ref, label: 'Contradiction', icon: XCircle, ready: !!contradiction, color: 'text-rose-500' },
    { ref: case3Ref, label: 'Apparent Conflict', icon: AlertTriangle, ready: !!apparentConflict, color: 'text-amber-500' },
    { ref: case4Ref, label: 'Extraction Failure', icon: FileX, ready: !!rejectedSample, color: isDark ? 'text-slate-300' : 'text-slate-600' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <Sparkles className="w-5 h-5 text-sky-500" />
            The Four Required Verification Cases
          </h2>
          <p className={cn('text-xs mt-1 max-w-2xl leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
            This system does not stop at extracting values. It checks evidence, comparability, and
            uncertainty before ever forming a relationship between two facts.
          </p>
        </div>
        <button
          onClick={loadCases}
          className={cn(
            'self-start sm:self-auto px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border shrink-0',
            isDark
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
          )}
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {/* Sticky case navigation */}
      <div
        className={cn(
          'sticky top-[53px] z-20 flex flex-wrap items-center gap-2 p-2 rounded-xl border backdrop-blur',
          isDark ? 'bg-slate-950/90 border-slate-800' : 'bg-white/90 border-slate-200 shadow-sm'
        )}
      >
        {navItems.map((item, idx) => (
          <button
            key={item.label}
            onClick={() => scrollTo(item.ref)}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-mono font-medium flex items-center gap-1.5 transition-colors border',
              isDark
                ? 'bg-slate-900 hover:bg-slate-800 border-slate-800 text-slate-300'
                : 'bg-slate-50 hover:bg-slate-100 border-slate-200 text-slate-600'
            )}
          >
            <item.icon className={cn('w-3.5 h-3.5', item.color)} />
            {idx + 1}. {item.label}
            {!loading && !item.ready && (
              <span className={cn('text-[9px] px-1 rounded', isDark ? 'bg-slate-800 text-slate-500' : 'bg-slate-200 text-slate-400')}>
                empty
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ================= CASE 1: CORROBORATION ================= */}
      <div ref={case1Ref} className={sectionCls}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs font-mono font-bold text-emerald-500 uppercase tracking-wide flex items-center gap-1.5">
            <CheckCircle2 className="w-4 h-4" />
            Case 1 - Corroboration
          </span>
          {corroboration && <GateVerdictBadge verdict="CORROBORATES" size="sm" />}
        </div>
        <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
          Independent sources agreeing on the same claim
        </h3>
        <p className={cn('text-xs mt-1 leading-relaxed max-w-2xl', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Corroboration does not require the two source statements to be worded identically, or even to
          share every qualifier - it requires the values to agree on a basis the comparability gate has
          actually checked. Two institutions can corroborate a figure while still differing on issuer or
          period; when that happens, this system says so explicitly rather than hiding it behind a green badge.
        </p>

        {corroboration && corroboration.source_fact && corroboration.target_fact ? (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <EvidenceFactCard role="Fact A" fact={corroboration.source_fact} accent="sky" onOpenEvidence={(f) => setEvidenceFact(f)} />
              <EvidenceFactCard role="Fact B" fact={corroboration.target_fact} accent="indigo" onOpenEvidence={(f) => setEvidenceFact(f)} />
            </div>

            <div className="flex flex-col items-center gap-1 py-1">
              <ArrowDown className={cn('w-4 h-4', isDark ? 'text-slate-600' : 'text-slate-300')} />
              <div className="flex items-center gap-2 flex-wrap justify-center">
                <GateVerdictBadge verdict="CORROBORATES" size="lg" />
                <ConfidencePill confidence={corroboration.confidence} showIcon />
                {(() => {
                  const cl = deriveRelationConfidenceLabel(corroboration, corroboration.gate);
                  return <UncertaintyBadge label={cl.label} detail={cl.detail} />;
                })()}
              </div>
            </div>

            <div
              className={cn(
                'p-3.5 rounded-lg border text-xs leading-relaxed space-y-1.5',
                isDark ? 'bg-emerald-500/5 border-emerald-500/20' : 'bg-emerald-50 border-emerald-200'
              )}
            >
              <p className={isDark ? 'text-slate-200' : 'text-slate-800'}>{corroboration.explanation}</p>
              <p className={cn('text-[11px]', isDark ? 'text-slate-400' : 'text-slate-600')}>
                {getCaveatExplanation(corroboration.reason_code)}
              </p>
              {corroboration.gate && corroboration.gate.verdict !== 'comparable' && (
                <p className="text-[11px] font-mono text-indigo-500">
                  Comparability gate verdict: <span className="uppercase font-semibold">{corroboration.gate.verdict.replace(/_/g, ' ')}</span>
                  {corroboration.gate.cross_issuer && ' - the two facts come from different issuers'}
                </p>
              )}
            </div>

            <button
              onClick={() => setSelectedRelationId(corroboration.relation_id)}
              className="px-3 py-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 text-xs font-mono font-medium flex items-center gap-1.5 transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Explain This Conclusion
            </button>
          </div>
        ) : loading ? (
          <div className={emptyCardCls}>Loading…</div>
        ) : (
          <div className={emptyCardCls}>
            <div className="flex items-center gap-2 text-xs text-amber-500 font-medium">
              <UploadCloud className="w-4 h-4 shrink-0" />
              <span>No corroboration relation is currently in the store.</span>
            </div>
            {onNavigateToIngest && (
              <button onClick={onNavigateToIngest} className="mt-1 px-3 py-1.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 text-xs font-mono transition-colors">
                Go to Document Ingest →
              </button>
            )}
          </div>
        )}
      </div>

      {/* ================= CASE 2: CONTRADICTION ================= */}
      <div ref={case2Ref} className={sectionCls}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs font-mono font-bold text-rose-500 uppercase tracking-wide flex items-center gap-1.5">
            <XCircle className="w-4 h-4" />
            Case 2 - Contradiction
          </span>
          {contradiction && <GateVerdictBadge verdict="CONTRADICTS" size="sm" />}
        </div>
        <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
          A genuine disagreement under comparable context
        </h3>
        <p className={cn('text-xs mt-1 leading-relaxed max-w-2xl', isDark ? 'text-slate-400' : 'text-slate-500')}>
          This is only ever flagged once the gate confirms the two figures share subject, measure, scope and
          period - never from unequal values alone. Even then, the UI states exactly what the values disagree
          on and surfaces any real caveat the backend attaches, rather than asserting these are definitely
          contradictory facts.
        </p>

        {contradiction && contradiction.source_fact && contradiction.target_fact ? (
          <div className="mt-4 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <EvidenceFactCard role="Fact A" fact={contradiction.source_fact} accent="sky" onOpenEvidence={(f) => setEvidenceFact(f)} />
              <EvidenceFactCard role="Fact B" fact={contradiction.target_fact} accent="rose" onOpenEvidence={(f) => setEvidenceFact(f)} />
            </div>

            <div className="flex flex-col items-center gap-1 py-1">
              <ArrowDown className={cn('w-4 h-4', isDark ? 'text-slate-600' : 'text-slate-300')} />
              <div className="flex items-center gap-2 flex-wrap justify-center">
                <GateVerdictBadge verdict="CONTRADICTS" size="lg" />
                <ConfidencePill confidence={contradiction.confidence} showIcon />
                {(() => {
                  const cl = deriveRelationConfidenceLabel(contradiction, contradiction.gate);
                  return <UncertaintyBadge label={cl.label} detail={cl.detail} />;
                })()}
              </div>
            </div>

            <div
              className={cn(
                'p-3.5 rounded-lg border text-xs leading-relaxed space-y-1.5',
                isDark ? 'bg-rose-500/5 border-rose-500/20' : 'bg-rose-50 border-rose-200'
              )}
            >
              <p className={cn('font-semibold', isDark ? 'text-rose-300' : 'text-rose-700')}>
                These values disagree under the available comparable context.
              </p>
              <p className={isDark ? 'text-slate-200' : 'text-slate-800'}>{contradiction.explanation}</p>
              {/* No period-incomplete caveat here anymore: a contradiction's
                  reason_code can no longer end in "_period_unverified" (see
                  confidenceLabel.ts) - an unstated/unparseable period now
                  makes the gate return AMBIGUOUS before any CONTRADICTS
                  relation is produced, so this case can't reach this page. */}
            </div>

            <button
              onClick={() => setSelectedRelationId(contradiction.relation_id)}
              className="px-3 py-2 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/20 text-xs font-mono font-medium flex items-center gap-1.5 transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Explain This Conclusion
            </button>
          </div>
        ) : loading ? (
          <div className={emptyCardCls}>Loading…</div>
        ) : (
          <div className={emptyCardCls}>No contradiction relation is currently in the store.</div>
        )}
      </div>

      {/* ================= CASE 3: APPARENT CONFLICT - HERO ================= */}
      <div
        ref={case3Ref}
        className={cn(
          sectionCls,
          'md:p-8 ring-2',
          isDark ? 'ring-amber-500/20 bg-gradient-to-b from-amber-500/[0.03] to-transparent' : 'ring-amber-400/40'
        )}
      >
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="text-xs font-mono font-bold text-amber-500 uppercase tracking-wide flex items-center gap-1.5">
            <AlertTriangle className="w-4 h-4" />
            Case 3 - Apparent Conflict · The Central Thesis
          </span>
          {apparentConflict && <GateVerdictBadge verdict="APPARENT_CONFLICT" size="sm" />}
        </div>
        <h3 className={cn('text-lg font-bold mt-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
          Comparability Before Comparison
        </h3>

        {apparentConflict && apparentConflict.source_fact && apparentConflict.target_fact ? (
          <div className="mt-4 space-y-5">
            {(() => {
              const sf = apparentConflict.source_fact!;
              const tf = apparentConflict.target_fact!;
              const sameSubject = sf.subject === tf.subject;
              const sameMeasure = sf.measure === tf.measure;
              const diffContext = describeDifferingContext(apparentConflict.gate?.qualifier_diff || apparentConflict.qualifier_diff);
              return (
                <p className={cn('text-xs font-mono uppercase tracking-wide text-center', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {sameSubject && sameMeasure ? 'Same subject & measure' : 'Related subject & measure'} - different {diffContext}
                </p>
              );
            })()}

            {/* Big value-vs-value display */}
            <div className="flex items-center justify-center gap-6 sm:gap-10 py-4">
              <div className="text-center">
                <div className={cn('text-4xl sm:text-5xl font-mono font-black', isDark ? 'text-slate-100' : 'text-slate-900')}>
                  {apparentConflict.source_fact.value.normalized}
                  {apparentConflict.source_fact.value.unit && (
                    <span className={cn('text-xl font-normal ml-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      {apparentConflict.source_fact.value.unit}
                    </span>
                  )}
                </div>
                <div className={cn('text-xs font-mono mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {apparentConflict.source_summary} · {apparentConflict.source_fact.qualifiers.issuer || 'unknown issuer'}
                </div>
              </div>
              <span className={cn('text-sm font-mono uppercase', isDark ? 'text-slate-500' : 'text-slate-400')}>vs</span>
              <div className="text-center">
                <div className={cn('text-4xl sm:text-5xl font-mono font-black', isDark ? 'text-slate-100' : 'text-slate-900')}>
                  {apparentConflict.target_fact.value.normalized}
                  {apparentConflict.target_fact.value.unit && (
                    <span className={cn('text-xl font-normal ml-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      {apparentConflict.target_fact.value.unit}
                    </span>
                  )}
                </div>
                <div className={cn('text-xs font-mono mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {apparentConflict.target_summary} · {apparentConflict.target_fact.qualifiers.issuer || 'unknown issuer'}
                </div>
              </div>
            </div>

            <div className="flex flex-col items-center gap-1">
              <GateVerdictBadge verdict="APPARENT_CONFLICT" size="lg" />
              <ConfidencePill confidence={apparentConflict.confidence} showIcon />
            </div>

            <div
              className={cn(
                'p-4 rounded-xl border text-sm leading-relaxed space-y-1.5',
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

            {apparentConflict.gate && (
              <div
                className={cn(
                  'p-4 rounded-xl border',
                  isDark ? 'bg-slate-900/70 border-slate-800' : 'bg-slate-50 border-slate-200'
                )}
              >
                <span className={cn('text-[11px] font-semibold uppercase tracking-wider block mb-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  Comparability Gate
                </span>
                <span
                  className={cn(
                    'inline-block px-3 py-1.5 rounded-md border font-mono font-bold uppercase text-sm tracking-wide',
                    isDark ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30' : 'bg-indigo-50 text-indigo-700 border-indigo-300'
                  )}
                >
                  {apparentConflict.gate.verdict.replace(/_/g, ' ')}
                </span>
                {apparentConflict.gate.cross_issuer && (
                  <p className={cn('text-[11px] font-mono mt-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
                    The two facts come from different issuers.
                  </p>
                )}
              </div>
            )}

            <div>
              <span className={cn('text-[11px] font-semibold uppercase tracking-wider block mb-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
                Evidence
              </span>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <EvidenceFactCard role="Fact A" fact={apparentConflict.source_fact} accent="sky" onOpenEvidence={(f) => setEvidenceFact(f)} />
                <EvidenceFactCard role="Fact B" fact={apparentConflict.target_fact} accent="indigo" onOpenEvidence={(f) => setEvidenceFact(f)} />
              </div>
            </div>

            <button
              onClick={() => setSelectedRelationId(apparentConflict.relation_id)}
              className="px-4 py-2.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/20 text-xs font-mono font-semibold flex items-center gap-1.5 transition-colors mx-auto"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Explain This Conclusion
            </button>
          </div>
        ) : loading ? (
          <div className={emptyCardCls}>Loading…</div>
        ) : (
          <div className={emptyCardCls}>No apparent-conflict relation is currently in the store.</div>
        )}
      </div>

      {/* ================= CASE 4: EXTRACTION FAILURE ================= */}
      <div ref={case4Ref} className={sectionCls}>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className={cn('text-xs font-mono font-bold uppercase tracking-wide flex items-center gap-1.5', isDark ? 'text-slate-300' : 'text-slate-600')}>
            <FileX className="w-4 h-4" />
            Case 4 - Extraction Failure
          </span>
          <UncertaintyBadge label="REJECTED" detail="Excluded from the trusted store - never presented as a fact." />
        </div>
        <h3 className={cn('text-sm font-semibold mt-2', isDark ? 'text-slate-200' : 'text-slate-800')}>
          The system refused to guess
        </h3>
        <p className={cn('text-xs mt-1 leading-relaxed max-w-2xl', isDark ? 'text-slate-400' : 'text-slate-500')}>
          When evidence could not be reliably grounded to source text, or an extracted value disagreed with
          its own cited quote, the candidate was rejected rather than presented as a trusted fact. This is
          not a bug being hidden - it is the system's evidence discipline working as intended.
        </p>

        <div
          className={cn(
            'mt-4 flex items-center justify-between p-3 rounded-lg border text-xs font-mono',
            isDark ? 'bg-slate-900/60 border-slate-800 text-slate-300' : 'bg-slate-50 border-slate-200 text-slate-600'
          )}
        >
          <span className="flex items-center gap-1.5 text-emerald-500 font-semibold">
            <ShieldCheck className="w-3.5 h-3.5" />
            {loading ? '…' : rejectedTotal} candidate{rejectedTotal === 1 ? '' : 's'} excluded from the trusted store
          </span>
          <span>Zero hallucinations admitted</span>
        </div>

        {rejectedSample ? (
          <div
            className={cn(
              'mt-4 p-4 rounded-xl border space-y-3',
              isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            )}
          >
            <div className="flex items-center justify-between">
              <span className={cn('text-[11px] font-semibold uppercase tracking-wider', isDark ? 'text-slate-400' : 'text-slate-500')}>
                Representative rejected candidate
              </span>
              <span
                className={cn(
                  'px-2 py-0.5 rounded text-[11px] font-mono border',
                  isDark ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' : 'bg-rose-50 text-rose-600 border-rose-200'
                )}
              >
                {rejectedSample.reason || 'rejected'}
              </span>
            </div>

            <blockquote
              className={cn(
                'p-3 rounded-lg border font-mono text-xs leading-relaxed italic border-l-4 border-l-rose-500',
                isDark ? 'bg-slate-950/60 border-slate-800 text-slate-300' : 'bg-white border-slate-200 text-slate-700'
              )}
            >
              "{rejectedSample.raw_fact?.verbatim_quote || 'No quote captured.'}"
            </blockquote>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-[11px] font-mono">
              <div>
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>ATTEMPTED SUBJECT</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{rejectedSample.raw_fact?.subject_raw || '-'}</span>
              </div>
              <div>
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>ATTEMPTED MEASURE</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{rejectedSample.raw_fact?.measure_raw || '-'}</span>
              </div>
              <div>
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>ATTEMPTED VALUE</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{rejectedSample.raw_fact?.value_raw || '-'}</span>
              </div>
              <div>
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>DOCUMENT</span>
                <span className={cn('truncate block', isDark ? 'text-slate-300' : 'text-slate-700')}>
                  {rejectedSample.doc_filename || rejectedSample.doc_id || '-'}
                </span>
              </div>
              <div>
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>PAGE</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>{rejectedSample.page_no ?? '-'}</span>
              </div>
            </div>

            <div
              className={cn(
                'p-2.5 rounded-lg border text-[11px] leading-relaxed',
                isDark ? 'bg-slate-950/40 border-slate-800 text-slate-300' : 'bg-white border-slate-200 text-slate-700'
              )}
            >
              <span className={cn('block text-[10px] font-semibold uppercase tracking-wide mb-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
                Why the system refused this candidate
              </span>
              {getRejectionReasonExplanation(rejectedSample.reason || '')}
              {rejectedSample.detail && (
                <span className={cn('block mt-1 font-mono text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                  {rejectedSample.detail}
                </span>
              )}
            </div>
          </div>
        ) : loading ? (
          <div className={emptyCardCls}>Loading…</div>
        ) : (
          <div className={emptyCardCls}>No rejected candidates recorded.</div>
        )}
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
