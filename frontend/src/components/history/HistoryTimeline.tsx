import React, { useEffect, useState } from 'react';
import { EntityHistory, FactSummary } from '@/types';
import { fetchEntityHistory, fetchEntityMeasures } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { Loader2, Clock, ChevronDown, ChevronRight, AlertTriangle, Search } from 'lucide-react';

// --------------------------------------------------------------------------
// Temporal Knowledge (fact_layer/temporal.py) - a chronologically ordered,
// scope/modality-grouped projection of every verified fact for one canonical
// (subject, measure).
//
// Red lines this view must not cross:
//  - It is NOT a time-series database. Every point is a stored Fact.
//  - It does NOT interpolate. An empty period is simply absent; a fact whose
//    period has no parsed start is reported under "ambiguous periods", never
//    guessed onto the axis.
//  - It does NOT invent temporal relationships. `related_points` only ever
//    contains relations the adjudicator actually recorded between two points
//    in this series. A fact merely being newer is not supersession.
// --------------------------------------------------------------------------

interface HistoryTimelineProps {
  subject: string;
  measure?: string;
  onOpenEvidence?: (fact: FactSummary) => void;
  collapsible?: boolean;
  defaultOpen?: boolean;
}

type MeasurePickerProps = {
  subject: string;
  onPick: (measure: string) => void;
};

const MeasurePicker: React.FC<MeasurePickerProps> = ({ subject, onPick }) => {
  const { isDark } = useTheme();
  const [measures, setMeasures] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMeasures(null);
    setError(null);
    fetchEntityMeasures(subject)
      .then((res) => { if (!cancelled) setMeasures(res.measures); })
      .catch((err) => { console.error('Measures load failed:', err); if (!cancelled) setError('Unknown subject.'); });
    return () => { cancelled = true; };
  }, [subject]);

  if (error) {
    return <p className={cn('text-[11px]', isDark ? 'text-slate-500' : 'text-slate-400')}>{error}</p>;
  }
  if (!measures) {
    return (
      <div className={cn('flex items-center gap-2 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Discovering measures…
      </div>
    );
  }
  if (measures.length === 0) {
    return <p className={cn('text-[11px]', isDark ? 'text-slate-500' : 'text-slate-400')}>No temporal facts for this entity.</p>;
  }
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-mono uppercase tracking-wider flex items-center gap-1.5">
        <Search className="w-3 h-3 text-sky-500" aria-hidden="true" />
        Pick a measure to see its timeline
      </div>
      <div className="flex flex-wrap gap-1.5">
        {measures.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onPick(m)}
            className={cn(
              'px-2.5 py-1 rounded border text-[11px] font-mono transition-colors',
              isDark
                ? 'bg-slate-900 border-slate-700 text-slate-300 hover:border-sky-500/50'
                : 'bg-white border-slate-200 text-slate-600 hover:border-sky-500/50'
            )}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  );
};

export const HistoryTimeline: React.FC<HistoryTimelineProps> = ({
  subject,
  measure,
  onOpenEvidence,
  collapsible = true,
  defaultOpen = false,
}) => {
  const { isDark } = useTheme();
  const [activeMeasure, setActiveMeasure] = useState<string | undefined>(measure);
  const [history, setHistory] = useState<EntityHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);
  const [expandedSeries, setExpandedSeries] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!activeMeasure) {
      setHistory(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchEntityHistory(subject, activeMeasure)
      .then((res) => { if (!cancelled) setHistory(res); })
      .catch((err) => { console.error('History load failed:', err); if (!cancelled) setError('Could not project this timeline.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [subject, activeMeasure]);

  const summaryRow = (
    <span className="flex items-center gap-1.5">
      <Clock className="w-3.5 h-3.5 text-emerald-500" aria-hidden="true" />
      Temporal history
      {activeMeasure ? ` · ${activeMeasure}` : ''}
      {history && ` · ${history.total_facts} facts`}
    </span>
  );

  const body = (
    <div className="space-y-3">
      {!activeMeasure ? (
        <MeasurePicker subject={subject} onPick={setActiveMeasure} />
      ) : error ? (
        <div className={cn('flex items-center gap-2 py-2 text-xs', isDark ? 'text-amber-400' : 'text-amber-700')}>
          <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
          {error}
        </div>
      ) : loading ? (
        <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
          Projecting timeline…
        </div>
      ) : history ? (
        <div className="space-y-3">
          {history.series.length === 0 && history.ambiguous_period_points.length === 0 ? (
            <p className={cn('text-xs py-2', isDark ? 'text-slate-500' : 'text-slate-400')}>
              No facts for this measure.
            </p>
          ) : (
            <>
              {/* Series, grouped by (scope, modality) */}
              {history.series.map((series) => {
                const key = `${series.scope}::${series.modality}`;
                const exp = expandedSeries[key] ?? true;
                return (
                  <div key={key} className={cn('rounded-lg border', isDark ? 'bg-slate-950/40 border-slate-800/80' : 'bg-white border-slate-200')}>
                    <button
                      type="button"
                      onClick={() => setExpandedSeries((m) => ({ ...m, [key]: !exp }))}
                      className={cn(
                        'w-full flex items-center justify-between gap-2 px-2.5 py-2 text-left',
                        isDark ? 'text-slate-200' : 'text-slate-700'
                      )}
                    >
                      <span className="flex items-center gap-1.5 font-mono text-[11px]">
                        {exp ? <ChevronDown className="w-3 h-3" aria-hidden="true" /> : <ChevronRight className="w-3 h-3" aria-hidden="true" />}
                        <span className="uppercase text-[10px] tracking-wider opacity-70">series</span>
                        <span className="text-sky-500">{series.scope}</span>
                        <span className="opacity-40">·</span>
                        <span className="text-violet-500">{series.modality}</span>
                      </span>
                      <span className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
                        {series.points.length} point{series.points.length === 1 ? '' : 's'}
                      </span>
                    </button>

                    {exp && (
                      <div className="border-t px-2.5 py-2 space-y-2" style={{ borderColor: isDark ? '#1e293b' : '#e2e8f0' }}>
                        {series.points.map((point) => (
                          <PointCard
                            key={point.fact_id}
                            point={point}
                            isDark={isDark}
                            onOpenEvidence={onOpenEvidence}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Ambiguous periods - reported separately, never placed on the axis */}
              {history.ambiguous_period_points.length > 0 && (
                <div className={cn('rounded-lg border', isDark ? 'bg-slate-950/40 border-amber-500/20' : 'bg-amber-50/40 border-amber-200')}>
                  <div className="px-2.5 py-2 flex items-center gap-1.5 text-[11px] font-mono">
                    <AlertTriangle className="w-3 h-3 text-amber-500" aria-hidden="true" />
                    <span className={isDark ? 'text-amber-400' : 'text-amber-700'}>
                      Ambiguous periods - {history.ambiguous_period_points.length} fact
                      {history.ambiguous_period_points.length === 1 ? '' : 's'} with no parsable start date. Never placed on the timeline.
                    </span>
                  </div>
                  <div className="border-t px-2.5 py-2 space-y-2" style={{ borderColor: isDark ? '#1e293b' : '#e2e8f0' }}>
                    {history.ambiguous_period_points.map((point) => (
                      <PointCard key={point.fact_id} point={point} isDark={isDark} onOpenEvidence={onOpenEvidence} />
                    ))}
                  </div>
                </div>
              )}

              <p className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
                {history.metadata.projection_of} · projected, not interpolated
                {history.metadata.interpolated ? '' : ' (never interpolated)'}
              </p>
            </>
          )}
        </div>
      ) : null}
    </div>
  );

  if (collapsible) {
    return (
      <div className={cn('rounded-xl border', isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200')}>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className={cn(
            'w-full px-3 py-2 text-[12px] font-medium flex items-center justify-between gap-2 text-left',
            isDark ? 'text-slate-300' : 'text-slate-600'
          )}
        >
          {summaryRow}
          <span className="text-[10px] font-mono opacity-70">{open ? 'hide' : 'show'}</span>
        </button>
        {open && (
          <div className="px-3 pb-3 border-t pt-3" style={{ borderColor: isDark ? '#1e293b' : '#e2e8f0' }}>
            {body}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={cn('rounded-xl border p-4 space-y-3', isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200')}>
      {summaryRow}
      {body}
    </div>
  );
};

const PointCard: React.FC<{
  point: import('@/types').HistoryPoint;
  isDark: boolean;
  onOpenEvidence?: (fact: FactSummary) => void;
}> = ({ point, isDark, onOpenEvidence }) => (
  <div className={cn('rounded-lg border p-2.5', isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200')}>
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={cn('font-mono text-xs font-bold', isDark ? 'text-slate-100' : 'text-slate-800')}>
            {point.value.normalized} {point.value.unit || ''}
          </span>
          {point.period.label && (
            <span className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
              · {point.period.label} ({point.period.kind})
            </span>
          )}
        </div>
        <div className={cn('text-[10px] font-mono mt-0.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
          {point.fact_id.slice(0, 12)} · {point.scope} · {point.modality}
          {point.issuer ? ` · ${point.issuer}` : ''}
        </div>
        {point.verification.value_verification !== 'verified' && (
          <div className="mt-1 text-[10px] font-mono">
            <span className={cn(
              point.verification.value_verification === 'unverified' || point.verification.value_verification === 'mismatch'
                ? 'text-amber-500'
                : isDark ? 'text-slate-500' : 'text-slate-400'
            )}>
              {point.verification.value_verification || (point.verification.evidence_verified ? 'evidence verified' : 'evidence unverified')}
            </span>
          </div>
        )}
      </div>
      <div className="shrink-0 space-y-1 flex flex-col items-end">
        <ConfidencePill confidence={point.confidence} />
        {onOpenEvidence && point.source.doc_id && (
          <button
            type="button"
            onClick={() => onOpenEvidence({ fact_id: point.fact_id } as FactSummary)}
            className={cn(
              'px-2 py-0.5 rounded border text-[10px] font-mono transition-colors',
              isDark
                ? 'bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 border-sky-500/20'
                : 'bg-sky-50 hover:bg-sky-100 text-sky-700 border-sky-200'
            )}
            title={`Open source page ${point.source.page ?? ''}`}
          >
            source
          </button>
        )}
      </div>
    </div>

    {/* Relations actually recorded between this point and another in this series */}
    {point.related_points.length > 0 && (
      <div className="mt-2 pt-2 border-t space-y-1" style={{ borderColor: isDark ? '#1e293b' : '#e2e8f0' }}>
        {point.related_points.map((rel) => (
          <div key={rel.relation_id} className="flex items-center gap-1.5 text-[10px] font-mono">
            <span className="text-rose-500">{rel.relation.replace(/_/g, ' ')}</span>
            <span className={isDark ? 'text-slate-500' : 'text-slate-400'}>→</span>
            <span className={isDark ? 'text-slate-300' : 'text-slate-600'}>{rel.fact_id.slice(0, 12)}</span>
          </div>
        ))}
      </div>
    )}
  </div>
);
