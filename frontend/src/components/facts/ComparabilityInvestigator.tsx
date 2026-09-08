import React, { useEffect, useState } from 'react';
import {
  ComparabilityExplanation,
  CounterfactualAction,
  DimensionReport,
  DimensionStatus,
  FactSummary,
} from '@/types';
import { fetchComparability } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import {
  Scale, Loader2, Check, X, CircleHelp, TriangleAlert, Minus,
  FileText, ChevronRight, ArrowRightLeft,
} from 'lucide-react';

// --------------------------------------------------------------------------
// Comparability Investigator.
//
// Renders `GET /facts/{a}/comparability/{b}` and nothing else — every string
// with semantic content (verdict, reason, required condition, conclusion)
// comes from the backend, which derives it from the existing gate. This file
// contains no comparability logic: duplicating it here is precisely how a
// frontend starts disagreeing with its own engine.
//
// Accessibility (§40): status is never conveyed by colour alone. Every status
// carries a glyph AND a text label AND an aria-label, and the matrix is a
// real <table> with scoped headers.
// --------------------------------------------------------------------------

interface Props {
  factA: FactSummary;
  factBId: string;
  /** How this pair came to be examined — shown so the evaluator can see that
   *  retrieval only proposed the candidate and the gate decided its meaning. */
  provenance?: string;
  onClose?: () => void;
}

const STATUS_META: Record<
  DimensionStatus,
  { glyph: string; label: string; tone: 'pass' | 'fail' | 'warn' | 'neutral' }
> = {
  match: { glyph: '✓', label: 'Match', tone: 'pass' },
  mismatch: { glyph: '✗', label: 'Mismatch', tone: 'fail' },
  missing: { glyph: '○', label: 'Not stated', tone: 'warn' },
  ambiguous: { glyph: '⚠', label: 'Ambiguous', tone: 'warn' },
  unverifiable: { glyph: '⚠', label: 'Unverifiable', tone: 'warn' },
  not_applicable: { glyph: '–', label: 'Not applicable', tone: 'neutral' },
};

const toneClasses = (tone: string, isDark: boolean) => {
  switch (tone) {
    case 'pass':
      return isDark ? 'text-emerald-400' : 'text-emerald-600';
    case 'fail':
      return isDark ? 'text-rose-400' : 'text-rose-600';
    case 'warn':
      return isDark ? 'text-amber-400' : 'text-amber-600';
    default:
      return isDark ? 'text-slate-500' : 'text-slate-400';
  }
};

const StatusIcon: React.FC<{ status: DimensionStatus; isDark: boolean }> = ({ status, isDark }) => {
  const meta = STATUS_META[status];
  const Icon =
    status === 'match' ? Check
      : status === 'mismatch' ? X
      : status === 'not_applicable' ? Minus
      : status === 'missing' ? CircleHelp
      : TriangleAlert;
  return (
    <span
      className={cn('inline-flex items-center gap-1 font-mono text-[10px]', toneClasses(meta.tone, isDark))}
      role="img"
      aria-label={meta.label}
    >
      <Icon className="w-3 h-3" aria-hidden="true" />
      {meta.label}
    </span>
  );
};

const factLine = (f: FactSummary) =>
  [f.value?.raw, f.qualifiers?.period?.label, f.qualifiers?.scope, f.qualifiers?.issuer]
    .filter(Boolean)
    .join(' · ');

const FactCard: React.FC<{ fact: FactSummary; label: string; isDark: boolean }> = ({
  fact, label, isDark,
}) => (
  <div className={cn('p-2.5 rounded-lg border', isDark ? 'bg-slate-950/40 border-slate-800' : 'bg-white border-slate-200')}>
    <div className={cn('text-[10px] font-mono uppercase tracking-wider mb-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
      {label}
    </div>
    <div className={cn('text-[12px] font-medium truncate', isDark ? 'text-slate-200' : 'text-slate-700')}>
      {fact.subject} · {fact.measure}
    </div>
    <div className={cn('text-[11px] font-mono mt-0.5', isDark ? 'text-slate-400' : 'text-slate-500')}>
      {factLine(fact) || '—'}
    </div>
    <div className={cn('text-[10px] font-mono mt-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
      {fact.doc_id ? `doc ${fact.doc_id.slice(0, 8)} · p${fact.page ?? '?'}` : 'no evidence anchor'}
    </div>
  </div>
);

const SectionLabel: React.FC<{ children: React.ReactNode; isDark: boolean }> = ({ children, isDark }) => (
  <h4
    className={cn(
      'text-[10px] font-semibold uppercase tracking-wider pb-1 mb-2 border-b',
      isDark ? 'text-slate-400 border-slate-800' : 'text-slate-500 border-slate-200'
    )}
  >
    {children}
  </h4>
);

const DimensionMatrix: React.FC<{ dimensions: DimensionReport[]; isDark: boolean }> = ({
  dimensions, isDark,
}) => (
  <div className="overflow-x-auto">
    <table className="w-full text-[11px] border-collapse">
      <caption className="sr-only">
        Dimension-by-dimension comparability check for the two facts
      </caption>
      <thead>
        <tr className={cn(isDark ? 'text-slate-500' : 'text-slate-400')}>
          <th scope="col" className="text-left font-mono font-normal py-1 pr-2">Dimension</th>
          <th scope="col" className="text-left font-mono font-normal py-1 pr-2">Fact A</th>
          <th scope="col" className="text-left font-mono font-normal py-1 pr-2">Fact B</th>
          <th scope="col" className="text-left font-mono font-normal py-1">Status</th>
        </tr>
      </thead>
      <tbody>
        {dimensions.map((d) => (
          <tr
            key={d.dimension}
            className={cn(
              'border-t',
              isDark ? 'border-slate-800/70' : 'border-slate-200',
              d.is_blocking && (isDark ? 'bg-rose-500/5' : 'bg-rose-50')
            )}
          >
            <th
              scope="row"
              className={cn('text-left font-normal py-1 pr-2 font-mono', isDark ? 'text-slate-300' : 'text-slate-600')}
            >
              {d.label}
              {d.is_blocking && (
                <span className={cn('ml-1 text-[9px] uppercase', isDark ? 'text-rose-400' : 'text-rose-600')}>
                  blocking
                </span>
              )}
            </th>
            <td className={cn('py-1 pr-2 font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
              {d.fact_a ?? <span className="italic opacity-60">not stated</span>}
            </td>
            <td className={cn('py-1 pr-2 font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
              {d.fact_b ?? <span className="italic opacity-60">not stated</span>}
            </td>
            <td className="py-1">
              <StatusIcon status={d.status} isDark={isDark} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const ActionCard: React.FC<{ action: CounterfactualAction; index: number; isDark: boolean }> = ({
  action, index, isDark,
}) => (
  <li className={cn('p-2.5 rounded-lg border', isDark ? 'bg-slate-950/40 border-slate-800' : 'bg-white border-slate-200')}>
    <div className="flex items-start gap-2">
      <span
        className={cn(
          'shrink-0 w-4 h-4 rounded-full text-[10px] font-mono grid place-items-center mt-0.5',
          isDark ? 'bg-slate-800 text-slate-300' : 'bg-slate-200 text-slate-600'
        )}
        aria-hidden="true"
      >
        {index + 1}
      </span>
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={cn('text-[11px] font-medium', isDark ? 'text-slate-200' : 'text-slate-700')}>
            {action.action_type.replace(/_/g, ' ')}
          </span>
          {!action.safe && (
            <span
              className={cn(
                'px-1 py-0.5 rounded text-[9px] font-mono uppercase border',
                isDark ? 'text-amber-400 border-amber-500/40' : 'text-amber-700 border-amber-300'
              )}
              title="This system will not perform this alignment automatically."
            >
              not auto-applied
            </span>
          )}
        </div>
        {(action.fact_a || action.fact_b) && (
          <div className={cn('text-[10px] font-mono flex items-center gap-1.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
            <span>{action.fact_a ?? 'not stated'}</span>
            <ArrowRightLeft className="w-3 h-3" aria-hidden="true" />
            <span>{action.fact_b ?? 'not stated'}</span>
          </div>
        )}
        <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-600')}>
          {action.required_condition}
        </p>
      </div>
    </div>
  </li>
);

const EvidenceRow: React.FC<{
  role: string; explanation: ComparabilityExplanation; isDark: boolean;
}> = ({ role, explanation, isDark }) => {
  const [open, setOpen] = useState(false);
  const ref = explanation.evidence_refs.find((r) => r.role === role);
  if (!ref || !ref.available) {
    return (
      <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
        {role === 'fact_a' ? 'Fact A' : 'Fact B'}: no evidence anchor recorded.
      </p>
    );
  }
  return (
    <div className={cn('rounded-lg border', isDark ? 'border-slate-800' : 'border-slate-200')}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full px-2.5 py-1.5 flex items-center justify-between gap-2 text-left"
      >
        <span className={cn('text-[11px] font-mono flex items-center gap-1.5', isDark ? 'text-slate-300' : 'text-slate-600')}>
          <FileText className="w-3 h-3" aria-hidden="true" />
          {role === 'fact_a' ? 'Fact A' : 'Fact B'} · doc {ref.doc_id?.slice(0, 8)} · p{ref.page}
        </span>
        <ChevronRight
          className={cn('w-3 h-3 transition-transform', open && 'rotate-90', isDark ? 'text-slate-600' : 'text-slate-400')}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div className={cn('px-2.5 pb-2.5 space-y-1 border-t', isDark ? 'border-slate-800' : 'border-slate-200')}>
          <p className={cn('text-[11px] italic pt-2', isDark ? 'text-slate-300' : 'text-slate-600')}>
            “{ref.verbatim_quote}”
          </p>
          <p className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
            chars {ref.char_start}–{ref.char_end}
            {ref.bbox ? ` · bbox [${ref.bbox.map((n) => Math.round(n)).join(', ')}]` : ''}
            {' · '}span {ref.verified ? 'verified' : 'unverified'}
            {ref.value_verification ? ` · value ${ref.value_verification}` : ''}
          </p>
        </div>
      )}
    </div>
  );
};

export const ComparabilityInvestigator: React.FC<Props> = ({ factA, factBId, provenance }) => {
  const { isDark } = useTheme();
  const [data, setData] = useState<ComparabilityExplanation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setLoading(true);
    fetchComparability(factA.fact_id, factBId)
      .then((res) => { if (!cancelled) setData(res); })
      .catch((err) => {
        console.error('Comparability investigation failed:', err);
        if (!cancelled) setError('Could not load the comparability explanation for this pair.');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [factA.fact_id, factBId]);

  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
        Evaluating the comparability gate…
      </div>
    );
  }

  if (error) {
    return (
      <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')} role="alert">
        {error} This is a display failure, not a finding about the two facts.
      </p>
    );
  }

  if (!data) return null;

  const blocked = !data.comparable && !data.relation_bearing;
  const verdictTone = data.comparable ? 'pass' : data.relation_bearing ? 'warn' : 'fail';

  return (
    <section
      className={cn('p-4 rounded-xl border space-y-4', isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200')}
      aria-label="Comparability investigator"
    >
      <h3
        className={cn(
          'text-[11px] font-semibold uppercase tracking-wider flex items-center gap-1.5 border-b pb-2',
          isDark ? 'text-slate-300 border-slate-800' : 'text-slate-600 border-slate-200'
        )}
      >
        <Scale className="w-3.5 h-3.5 text-sky-500" aria-hidden="true" />
        Comparability Investigator
      </h3>

      {provenance && (
        <p className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
          {provenance} — retrieval proposed this pair; the comparability gate decided what it means.
        </p>
      )}

      {/* 1. The two facts */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <FactCard fact={data.fact_a} label="Fact A" isDark={isDark} />
        <FactCard fact={data.fact_b} label="Fact B" isDark={isDark} />
      </div>

      {/* 2. Verdict — the single most important thing on screen */}
      <div
        className={cn(
          'p-3 rounded-lg border',
          verdictTone === 'pass'
            ? isDark ? 'bg-emerald-500/5 border-emerald-500/30' : 'bg-emerald-50 border-emerald-200'
            : verdictTone === 'warn'
            ? isDark ? 'bg-amber-500/5 border-amber-500/30' : 'bg-amber-50 border-amber-200'
            : isDark ? 'bg-rose-500/5 border-rose-500/30' : 'bg-rose-50 border-rose-200'
        )}
      >
        <p className={cn('text-[13px] font-semibold flex items-center gap-1.5', toneClasses(verdictTone, isDark))}>
          <span aria-hidden="true">{data.comparable ? '✓' : data.relation_bearing ? '⚠' : '✗'}</span>
          {data.comparable
            ? 'Comparison permitted'
            : data.relation_bearing
            ? 'Comparison permitted under a specific relationship'
            : 'Comparison blocked'}
        </p>
        <p className={cn('text-[10px] font-mono mt-0.5', isDark ? 'text-slate-500' : 'text-slate-500')}>
          gate verdict: {data.verdict} · reason: {data.gate_reason_code}
        </p>

        {data.blocking_reasons.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {data.blocking_reasons.map((br) => (
              <li key={`${br.dimension}-${br.reason_code}`} className={cn('text-[11px] font-mono', toneClasses('fail', isDark))}>
                <span aria-hidden="true">✗</span> {br.label} mismatch
                <span className={cn('ml-1', isDark ? 'text-slate-600' : 'text-slate-400')}>({br.reason_code})</span>
              </li>
            ))}
          </ul>
        )}

        <p className={cn('text-[11px] mt-2 leading-relaxed', isDark ? 'text-slate-300' : 'text-slate-600')}>
          {data.safe_conclusion}
        </p>
      </div>

      {/* 3. Caveats — ambiguity and verification, kept distinct from blocking */}
      {data.caveats.length > 0 && (
        <div>
          <SectionLabel isDark={isDark}>Caveats (not blocking)</SectionLabel>
          <ul className="space-y-1">
            {data.caveats.map((c, i) => (
              <li key={i} className={cn('text-[11px] flex items-start gap-1.5', isDark ? 'text-amber-400/90' : 'text-amber-700')}>
                <TriangleAlert className="w-3 h-3 mt-0.5 shrink-0" aria-hidden="true" />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 4. Dimension matrix */}
      <div>
        <SectionLabel isDark={isDark}>Comparability check</SectionLabel>
        <DimensionMatrix dimensions={data.dimensions} isDark={isDark} />
      </div>

      {/* 5. Counterfactual readiness */}
      {data.counterfactual_actions.length > 0 && (
        <div>
          <SectionLabel isDark={isDark}>What would make them comparable?</SectionLabel>
          <ol className="space-y-1.5">
            {data.counterfactual_actions.map((a, i) => (
              <ActionCard key={a.dimension} action={a} index={i} isDark={isDark} />
            ))}
          </ol>
          <p className={cn('text-[10px] mt-1.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
            These are requirements for a valid comparison, not operations that were performed. No fact
            was modified, and the system does not claim the required information exists in this corpus.
          </p>
        </div>
      )}

      {/* 6. Evidence */}
      <div>
        <SectionLabel isDark={isDark}>Source evidence</SectionLabel>
        <div className="space-y-1.5">
          <EvidenceRow role="fact_a" explanation={data} isDark={isDark} />
          <EvidenceRow role="fact_b" explanation={data} isDark={isDark} />
        </div>
      </div>

      {/* 7. Persistent footer — the distinction the whole feature protects */}
      {blocked && (
        <p
          className={cn(
            'text-[11px] p-2 rounded-lg border',
            isDark ? 'bg-slate-950/40 border-slate-800 text-slate-400' : 'bg-white border-slate-200 text-slate-600'
          )}
        >
          <strong>Comparison blocked — this is not a finding that the two facts are unrelated.</strong>{' '}
          It means this system cannot validly compare them under the current gate.
        </p>
      )}
    </section>
  );
};
