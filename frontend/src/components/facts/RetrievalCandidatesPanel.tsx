import React, { useEffect, useState } from 'react';
import { FactSummary, CandidateMatch, CandidateFunnel } from '@/types';
import { fetchFactCandidates } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { Radar, Loader2, ShieldOff, ArrowRight } from 'lucide-react';

interface RetrievalCandidatesPanelProps {
  fact: FactSummary;
}

// Compact funnel row: label + count, sized relative to the largest count
// in the funnel so the shrink from "Lexical" down to "Final Top-K" reads
// visually, not just numerically.
const FunnelRow: React.FC<{ label: string; count: number; max: number; isDark: boolean }> = ({
  label,
  count,
  max,
  isDark,
}) => (
  <div className="flex items-center gap-2 text-[11px]">
    <span className={cn('w-20 shrink-0 font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>{label}</span>
    <div className={cn('flex-1 h-1.5 rounded-full overflow-hidden', isDark ? 'bg-slate-800' : 'bg-slate-200')}>
      <div
        className="h-full rounded-full bg-sky-500"
        style={{ width: `${max > 0 ? Math.max((count / max) * 100, count > 0 ? 4 : 0) : 0}%` }}
      />
    </div>
    <span className={cn('w-16 text-right font-mono shrink-0', isDark ? 'text-slate-300' : 'text-slate-600')}>
      {count} {count === 1 ? 'candidate' : 'candidates'}
    </span>
  </div>
);

const ScoreBar: React.FC<{ label: string; value: number; isDark: boolean }> = ({ label, value, isDark }) => (
  <div className="flex items-center gap-1.5 text-[10px]">
    <span className={cn('w-14 shrink-0 font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>{label}</span>
    <div className={cn('flex-1 h-1 rounded-full overflow-hidden', isDark ? 'bg-slate-800' : 'bg-slate-200')}>
      <div
        className={cn('h-full rounded-full', isDark ? 'bg-slate-500' : 'bg-slate-400')}
        style={{ width: `${Math.max(Math.min(value, 1), 0) * 100}%` }}
      />
    </div>
    <span className={cn('w-10 text-right font-mono shrink-0', isDark ? 'text-slate-400' : 'text-slate-500')}>
      {value.toFixed(2)}
    </span>
  </div>
);

const CandidateRow: React.FC<{ candidate: CandidateMatch; isDark: boolean }> = ({ candidate, isDark }) => {
  const blocked = candidate.blocking_status === 'blocked';
  const other = candidate.fact_summary;
  return (
    <div
      className={cn(
        'p-2.5 rounded-lg border space-y-1.5',
        blocked
          ? isDark ? 'bg-slate-950/40 border-slate-800/60 opacity-60' : 'bg-slate-50 border-slate-200 opacity-70'
          : isDark ? 'bg-slate-950/40 border-slate-800' : 'bg-white border-slate-200'
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={cn('font-mono text-[11px] truncate', isDark ? 'text-slate-300' : 'text-slate-600')}>
          {other ? `${other.subject}::${other.measure}` : candidate.fact_id}
        </span>
        {blocked ? (
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-slate-700/30 text-slate-400 border border-slate-700 shrink-0"
            title={`Blocked: ${candidate.blocking_reason}`}
          >
            <ShieldOff className="w-3 h-3" />
            {candidate.blocking_reason}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide bg-sky-500/10 text-sky-500 border border-sky-500/30 shrink-0">
            candidate
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-1">
        <ScoreBar label="lexical" value={candidate.lexical_score} isDark={isDark} />
        <ScoreBar label="semantic" value={candidate.semantic_score} isDark={isDark} />
        <ScoreBar label="hybrid" value={candidate.hybrid_score} isDark={isDark} />
      </div>
    </div>
  );
};

// Section 19's exact intent: this panel says "retrieval found this
// candidate", never "retrieval decided these facts contradict". The
// comparability gate remains the sole authority for that — a blocked
// candidate is shown as "not sent to the gate", not as "incomparable" or
// any other gate-owned verdict.
export const RetrievalCandidatesPanel: React.FC<RetrievalCandidatesPanelProps> = ({ fact }) => {
  const { isDark } = useTheme();
  const [funnel, setFunnel] = useState<CandidateFunnel | null>(null);
  const [candidates, setCandidates] = useState<CandidateMatch[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    setFunnel(null);
    setCandidates(null);
    setError(false);
    setLoading(true);
    fetchFactCandidates(fact.fact_id, 10)
      .then((res) => {
        setFunnel(res.funnel);
        setCandidates(res.candidates);
      })
      .catch((err) => {
        console.error('Failed to load retrieval candidates:', err);
        setError(true);
      })
      .finally(() => setLoading(false));
  }, [fact.fact_id]);

  const maxFunnel = funnel ? Math.max(funnel.lexical_count, funnel.semantic_count, 1) : 1;

  return (
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
        <Radar className="w-3.5 h-3.5 text-sky-500" />
        Candidate Retrieval
      </span>

      {loading ? (
        <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Running lexical + semantic retrieval…
        </div>
      ) : error ? (
        <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')}>
          Unable to load retrieval candidates for this fact right now.
        </p>
      ) : (
        <div className="space-y-4">
          {funnel && (
            <div className="space-y-1.5">
              <FunnelRow label="Lexical" count={funnel.lexical_count} max={maxFunnel} isDark={isDark} />
              <FunnelRow label="Semantic" count={funnel.semantic_count} max={maxFunnel} isDark={isDark} />
              <FunnelRow label="After block" count={funnel.after_block_count} max={maxFunnel} isDark={isDark} />
              <FunnelRow label="Final Top-K" count={funnel.final_top_k_count} max={maxFunnel} isDark={isDark} />
            </div>
          )}

          {candidates && candidates.length > 0 ? (
            <div className="space-y-1.5">
              {candidates.map((c) => (
                <CandidateRow key={c.fact_id} candidate={c} isDark={isDark} />
              ))}
            </div>
          ) : (
            <p className={cn('text-xs py-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
              No candidates retrieved for this fact.
            </p>
          )}

          <p className={cn('text-[10px] flex items-center gap-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
            Retrieval found these candidates
            <ArrowRight className="w-3 h-3" />
            the comparability gate decides what they mean, above.
          </p>
        </div>
      )}
    </div>
  );
};
