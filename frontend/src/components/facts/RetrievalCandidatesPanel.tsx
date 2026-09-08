import React, { useEffect, useState } from 'react';
import { FactSummary, CandidateMatch, RetrievalDiagnostics } from '@/types';
import { fetchFactCandidates } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { Radar, Loader2, ShieldOff, ChevronRight, StepForward, CircleSlash } from 'lucide-react';

interface RetrievalCandidatesPanelProps {
  fact: FactSummary;
}

// --------------------------------------------------------------------------
// Language contract (task section 19). This panel narrates a SEARCH, not a
// set of conclusions:
//   "retrieved candidate"        not  "related fact"
//   "comparison blocked"         not  "facts are unrelated"
//   "search budget exhausted"    not  "no relationship exists"
// The comparability gate and the adjudicator remain the only authorities
// for meaning, and each gets its own visually distinct section below so a
// reader cannot mistake a retrieval score for a verdict.
// --------------------------------------------------------------------------

const TERMINATION_COPY: Record<string, string> = {
  sufficient_candidates: 'Sufficient candidate coverage within budget',
  no_further_candidates: 'No further candidates exist for this fact',
  budget_exhausted: 'Search budget exhausted — more candidates may exist',
  no_candidates_found: 'No candidate surfaced within the retrieval budget',
};

const EXPANSION_COPY: Record<string, string> = {
  insufficient_unblocked_candidates: 'too few comparable candidates at this budget',
  candidate_set_saturated: 'candidate set was saturated — neighbourhood likely continues',
};

const GATE_REASON_COPY: Record<string, string> = {
  period_disjoint: 'Period disjoint',
  period_overlap: 'Period overlap',
  scope_mismatch: 'Scope mismatch',
  unit_mismatch: 'Unit / currency mismatch',
  segment_mismatch: 'Segment mismatch',
  basis_mismatch: 'Basis mismatch',
  value_kind_mismatch: 'Value kind mismatch',
  forecast_disagreement: 'Issuer forecast disagreement',
};

const prettyGateReason = (code: string) =>
  GATE_REASON_COPY[code] ?? code.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());

const Stat: React.FC<{ label: string; value: React.ReactNode; isDark: boolean; muted?: boolean }> = ({
  label,
  value,
  isDark,
  muted,
}) => (
  <div className="flex items-baseline justify-between gap-3 text-[11px] leading-5">
    <span className={cn('font-mono', muted ? (isDark ? 'text-slate-500' : 'text-slate-400') : isDark ? 'text-slate-400' : 'text-slate-500')}>
      {label}
    </span>
    <span className={cn('font-mono tabular-nums', isDark ? 'text-slate-200' : 'text-slate-700')}>{value}</span>
  </div>
);

const SectionLabel: React.FC<{ children: React.ReactNode; isDark: boolean }> = ({ children, isDark }) => (
  <div
    className={cn(
      'text-[10px] font-semibold uppercase tracking-wider pb-1 mb-1.5 border-b',
      isDark ? 'text-slate-400 border-slate-800' : 'text-slate-500 border-slate-200'
    )}
  >
    {children}
  </div>
);

// The K ladder, rendered as the path actually taken: 10 → 25 → 50.
const LadderTrail: React.FC<{ diagnostics: RetrievalDiagnostics; isDark: boolean }> = ({ diagnostics, isDark }) => {
  const { policy } = diagnostics;
  const steps = policy.stages.map((s) => s.k);
  return (
    <div className="flex items-center flex-wrap gap-1 text-[11px] font-mono">
      {steps.map((k, i) => (
        <React.Fragment key={`${k}-${i}`}>
          {i > 0 && <ChevronRight className={cn('w-3 h-3', isDark ? 'text-slate-600' : 'text-slate-400')} />}
          <span
            className={cn(
              'px-1.5 py-0.5 rounded border tabular-nums',
              i === steps.length - 1
                ? 'bg-sky-500/10 text-sky-500 border-sky-500/30'
                : isDark
                ? 'bg-slate-800/60 text-slate-400 border-slate-700'
                : 'bg-slate-100 text-slate-500 border-slate-200'
            )}
          >
            K={k}
          </span>
        </React.Fragment>
      ))}
      <span className={cn('ml-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
        (ceiling {policy.max_k})
      </span>
    </div>
  );
};

const ScoreBar: React.FC<{ label: string; value: number; isDark: boolean }> = ({ label, value, isDark }) => (
  <div className="flex items-center gap-1.5 text-[10px]">
    <span className={cn('w-14 shrink-0 font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>{label}</span>
    <div className={cn('flex-1 h-1 rounded-full overflow-hidden', isDark ? 'bg-slate-800' : 'bg-slate-200')}>
      <div
        className={cn('h-full rounded-full', isDark ? 'bg-slate-500' : 'bg-slate-400')}
        style={{ width: `${Math.max(Math.min(value, 1), 0) * 100}%` }}
      />
    </div>
    <span className={cn('w-10 text-right font-mono shrink-0 tabular-nums', isDark ? 'text-slate-400' : 'text-slate-500')}>
      {value.toFixed(2)}
    </span>
  </div>
);

const CandidateRow: React.FC<{ candidate: CandidateMatch; isDark: boolean }> = ({ candidate, isDark }) => {
  const [open, setOpen] = useState(false);
  const blocked = candidate.blocking_status === 'blocked';
  const other = candidate.fact_summary;
  return (
    <div
      className={cn(
        'rounded-lg border',
        blocked
          ? isDark
            ? 'bg-slate-950/40 border-slate-800/60 opacity-60'
            : 'bg-slate-50 border-slate-200 opacity-70'
          : isDark
          ? 'bg-slate-950/40 border-slate-800'
          : 'bg-white border-slate-200'
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full p-2.5 flex items-center justify-between gap-2 text-left"
      >
        <span className={cn('font-mono text-[11px] truncate', isDark ? 'text-slate-300' : 'text-slate-600')}>
          {other ? `${other.subject}::${other.measure}` : candidate.fact_id}
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          {blocked ? (
            <span
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-slate-700/30 text-slate-400 border border-slate-700"
              title={`Comparison blocked before the gate: ${candidate.blocking_reason}`}
            >
              <ShieldOff className="w-3 h-3" />
              {candidate.blocking_reason}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-sky-500/10 text-sky-500 border border-sky-500/30">
              retrieved
            </span>
          )}
          <ChevronRight
            className={cn('w-3 h-3 transition-transform', open && 'rotate-90', isDark ? 'text-slate-600' : 'text-slate-400')}
          />
        </span>
      </button>

      {open && (
        <div className={cn('px-2.5 pb-2.5 space-y-2 border-t', isDark ? 'border-slate-800' : 'border-slate-200')}>
          {other && (
            <div className="grid grid-cols-2 gap-x-3 pt-2">
              <Stat label="document" value={other.doc_id ?? '—'} isDark={isDark} />
              <Stat label="page" value={other.page ?? '—'} isDark={isDark} />
              <Stat label="value" value={other.value?.raw ?? '—'} isDark={isDark} />
              <Stat label="period" value={other.qualifiers?.period?.label ?? '—'} isDark={isDark} />
              <Stat label="scope" value={other.qualifiers?.scope ?? '—'} isDark={isDark} />
              <Stat label="issuer" value={other.qualifiers?.issuer ?? '—'} isDark={isDark} />
            </div>
          )}
          <div className="space-y-1">
            <div className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
              retrieval relevance (not confidence)
            </div>
            <ScoreBar label="lexical" value={candidate.lexical_score} isDark={isDark} />
            <ScoreBar label="semantic" value={candidate.semantic_score} isDark={isDark} />
            <ScoreBar label="hybrid" value={candidate.hybrid_score} isDark={isDark} />
          </div>
          {blocked && (
            <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
              Comparison blocked before the gate ({candidate.blocking_reason}). This is not a finding that the two
              facts are unrelated.
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export const RetrievalCandidatesPanel: React.FC<RetrievalCandidatesPanelProps> = ({ fact }) => {
  const { isDark } = useTheme();
  const [diagnostics, setDiagnostics] = useState<RetrievalDiagnostics | null>(null);
  const [candidates, setCandidates] = useState<CandidateMatch[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    setDiagnostics(null);
    setCandidates(null);
    setError(false);
    setLoading(true);
    fetchFactCandidates(fact.fact_id, 10)
      .then((res) => {
        setDiagnostics(res.diagnostics);
        setCandidates(res.candidates);
      })
      .catch((err) => {
        console.error('Failed to load retrieval candidates:', err);
        setError(true);
      })
      .finally(() => setLoading(false));
  }, [fact.fact_id]);

  const gateReasons = diagnostics ? Object.entries(diagnostics.gate.reasons) : [];
  const relationships = diagnostics ? Object.entries(diagnostics.relationships) : [];

  return (
    <div className={cn('p-4 rounded-xl border', isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200')}>
      <span
        className={cn(
          'text-[11px] font-semibold uppercase tracking-wider flex items-center gap-1.5 border-b pb-2 mb-3',
          isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
        )}
      >
        <Radar className="w-3.5 h-3.5 text-sky-500" />
        Candidate Retrieval
      </span>

      {loading ? (
        <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Running adaptive lexical + semantic retrieval…
        </div>
      ) : error ? (
        <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')}>
          Retrieval diagnostics are unavailable for this fact right now. This says nothing about whether the fact has
          relationships — the relations shown above come from the store, not from retrieval.
        </p>
      ) : diagnostics ? (
        <div className="space-y-4">
          {/* 1. What the search did */}
          <div>
            <SectionLabel isDark={isDark}>Adaptive search</SectionLabel>
            <LadderTrail diagnostics={diagnostics} isDark={isDark} />
            <div className="mt-1.5">
              <Stat label="final K" value={`${diagnostics.policy.final_k} / ${diagnostics.policy.max_k}`} isDark={isDark} />
              <Stat label="rounds" value={diagnostics.policy.rounds} isDark={isDark} />
            </div>
            {diagnostics.policy.expansion_reasons.length > 0 && (
              <div className={cn('mt-1.5 text-[10px] space-y-0.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
                {diagnostics.policy.expansion_reasons.map((r, i) => (
                  <div key={i} className="flex items-start gap-1">
                    <StepForward className="w-3 h-3 mt-0.5 shrink-0" />
                    <span>Expanded because {EXPANSION_COPY[r] ?? r.replace(/_/g, ' ')}.</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 2. What was searched vs excluded */}
          <div>
            <SectionLabel isDark={isDark}>Candidates</SectionLabel>
            <Stat label="retrieved" value={diagnostics.counts.retrieved_unique} isDark={isDark} />
            <Stat label="searchable bucket" value={`${diagnostics.blocking.bucket_size} of ${diagnostics.blocking.corpus_size} facts`} isDark={isDark} />
            <Stat
              label="excluded by blocking"
              value={diagnostics.blocking.excluded_by_blocking}
              isDark={isDark}
              muted
            />
            <p className={cn('mt-1 text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
              Blocking runs as a search restriction: facts with a different subject, measure or value kind are never
              retrieved rather than retrieved and discarded.
            </p>
          </div>

          {/* 3. Channels — explicitly overlapping */}
          <div>
            <SectionLabel isDark={isDark}>Retrieval channels</SectionLabel>
            <Stat label="lexical" value={diagnostics.channels.lexical_unique} isDark={isDark} />
            <Stat label="semantic" value={diagnostics.channels.semantic_unique} isDark={isDark} />
            <Stat label="found by both" value={diagnostics.channels.both_channels} isDark={isDark} muted />
            <Stat label="distinct (union)" value={diagnostics.channels.union_unique} isDark={isDark} />
            <p className={cn('mt-1 text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
              Channel counts overlap — they do not add up. “Distinct” is the union.
            </p>
          </div>

          {/* 4. The gate — authoritative */}
          <div>
            <SectionLabel isDark={isDark}>Comparability gate (authoritative)</SectionLabel>
            <Stat label="evaluated" value={diagnostics.gate.evaluated} isDark={isDark} />
            <Stat label="comparable" value={diagnostics.gate.comparable} isDark={isDark} />
            {diagnostics.gate.relation_bearing > 0 && (
              <Stat label="succession / aggregation" value={diagnostics.gate.relation_bearing} isDark={isDark} />
            )}
            <Stat label="incomparable" value={diagnostics.gate.incomparable} isDark={isDark} />
            {diagnostics.gate.same_page_skipped > 0 && (
              <Stat label="same-page, skipped" value={diagnostics.gate.same_page_skipped} isDark={isDark} muted />
            )}
            {gateReasons.length > 0 && (
              <div className="mt-1.5 space-y-0.5">
                {gateReasons.map(([code, n]) => (
                  <Stat key={code} label={prettyGateReason(code)} value={n} isDark={isDark} muted />
                ))}
              </div>
            )}
          </div>

          {/* 5. Relationships — from the adjudicator */}
          <div>
            <SectionLabel isDark={isDark}>Relationships established</SectionLabel>
            {relationships.length > 0 ? (
              relationships.map(([kind, n]) => (
                <Stat key={kind} label={kind.replace(/_/g, ' ')} value={n} isDark={isDark} />
              ))
            ) : (
              <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                No relationship was established among the candidates examined.
              </p>
            )}
          </div>

          {/* 6. Candidate list */}
          {candidates && candidates.length > 0 ? (
            <div>
              <SectionLabel isDark={isDark}>Retrieved candidates</SectionLabel>
              <div className="space-y-1.5">
                {candidates.map((c) => (
                  <CandidateRow key={c.fact_id} candidate={c} isDark={isDark} />
                ))}
              </div>
            </div>
          ) : (
            <p className={cn('text-xs', isDark ? 'text-slate-500' : 'text-slate-400')}>
              No candidate surfaced within the retrieval budget.
            </p>
          )}

          {/* 7. Timing */}
          <div>
            <SectionLabel isDark={isDark}>Timing</SectionLabel>
            <Stat label="retrieval" value={`${(diagnostics.timing.lexical_ms + diagnostics.timing.semantic_ms + diagnostics.timing.fusion_ms).toFixed(1)} ms`} isDark={isDark} />
            <Stat label="gate" value={`${diagnostics.timing.gate_ms.toFixed(1)} ms`} isDark={isDark} />
            <Stat label="adjudication" value={`${diagnostics.timing.adjudication_ms.toFixed(1)} ms`} isDark={isDark} />
            <Stat label="total" value={`${diagnostics.timing.total_ms.toFixed(1)} ms`} isDark={isDark} />
          </div>

          {/* 8. Why the search stopped — always shown */}
          <div
            className={cn(
              'p-2.5 rounded-lg border flex items-start gap-2',
              diagnostics.termination.budget_exhausted
                ? isDark
                  ? 'bg-amber-500/5 border-amber-500/30'
                  : 'bg-amber-50 border-amber-200'
                : isDark
                ? 'bg-slate-950/40 border-slate-800'
                : 'bg-white border-slate-200'
            )}
          >
            <CircleSlash
              className={cn(
                'w-3.5 h-3.5 mt-0.5 shrink-0',
                diagnostics.termination.budget_exhausted ? 'text-amber-500' : isDark ? 'text-slate-500' : 'text-slate-400'
              )}
            />
            <div className="space-y-1">
              <p className={cn('text-[11px]', isDark ? 'text-slate-300' : 'text-slate-600')}>
                Search stopped: {TERMINATION_COPY[diagnostics.termination.reason] ?? diagnostics.termination.reason}
              </p>
              <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                Retrieval is a bounded search. A candidate it did not reach was not examined — that is not a finding
                that no relationship exists. The comparability gate above decides meaning.
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};
