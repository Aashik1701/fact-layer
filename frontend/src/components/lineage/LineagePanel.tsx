import React, { useEffect, useState } from 'react';
import { LineageResult, GraphNodeType, FactSummary } from '@/types';
import { fetchFactLineage, fetchRelationLineage } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { NODE_STYLE } from '@/components/graph/KnowledgeGraphCanvas';
import { Loader2, GitBranch, FileSearch, AlertTriangle } from 'lucide-react';

// --------------------------------------------------------------------------
// Evidence Lineage (fact_layer/lineage.py) - the provenance chain behind a
// stored conclusion.
//
//   CONCLUSION (relation, or the fact itself)
//       |
//    FACT(S)
//       |
//    EVIDENCE
//       |
//      PAGE
//       |
//    DOCUMENT
//
// This is a READ-ONLY projection; it never invents confidence and never
// fabricates a relationship. `facts`/`evidence`/`documents` are the same
// graph nodes re-categorized for a left-to-right "what does this rest on?"
// reading. A pair of facts with no stored relation is a "comparison blocked"
// case - rendered as such, never as a relation.
// --------------------------------------------------------------------------

interface LineagePanelProps {
  /** Set one (and only one). Fact lineage for a single fact, relation
   *  lineage for an adjudicated relationship. */
  factId?: string;
  relationId?: string;
  onOpenEvidence?: (fact: FactSummary) => void;
  collapsible?: boolean;
  defaultOpen?: boolean;
}

const kindColumn: GraphNodeType[] = ['fact', 'evidence', 'document'];

export const LineagePanel: React.FC<LineagePanelProps> = ({
  factId,
  relationId,
  onOpenEvidence,
  collapsible = true,
  defaultOpen = false,
}) => {
  const { isDark } = useTheme();
  const [data, setData] = useState<LineageResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const root = data?.root_conclusion ?? null;

  useEffect(() => {
    const id = factId || relationId;
    if (!id) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const p = factId
      ? fetchFactLineage(factId)
      : relationId
        ? fetchRelationLineage(relationId)
        : Promise.reject(new Error('Neither fact nor relation id provided'));
    p.then((res) => { if (!cancelled) setData(res); })
      .catch((err) => { console.error('Lineage load failed:', err); if (!cancelled) setError('Could not resolve this conclusion’s provenance chain.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [factId, relationId]);

  const summaryRow = (
    <span className="flex items-center gap-1.5">
      <GitBranch className="w-3.5 h-3.5 text-sky-500" aria-hidden="true" />
      {root && root.relation_id
        ? `Evidence lineage · ${data?.facts.length ?? 0} facts · ${data?.evidence.length ?? 0} evidence · ${data?.documents.length ?? 0} documents`
        : `Evidence lineage · ${data?.evidence.length ?? 0} evidence · ${data?.documents.length ?? 0} documents`}
    </span>
  );

  if (collapsible) {
    return (
      <div
        className={cn(
          'rounded-xl border',
          isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
        )}
      >
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
            <Body
              data={data}
              loading={loading}
              error={error}
              isDark={isDark}
              onOpenEvidence={onOpenEvidence}
              factId={factId}
              relationId={relationId}
              expanded={expanded}
              setExpanded={setExpanded}
            />
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'rounded-xl border p-4 space-y-3',
        isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-slate-50 border-slate-200'
      )}
    >
      {summaryRow}
      <Body
        data={data}
        loading={loading}
        error={error}
        isDark={isDark}
        onOpenEvidence={onOpenEvidence}
        factId={factId}
        relationId={relationId}
        expanded={expanded}
        setExpanded={setExpanded}
      />
    </div>
  );
};

const Body: React.FC<{
  data: LineageResult | null;
  loading: boolean;
  error: string | null;
  isDark: boolean;
  onOpenEvidence?: (fact: FactSummary) => void;
  factId?: string;
  relationId?: string;
  expanded: Record<string, boolean>;
  setExpanded: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
}> = ({ data, loading, error, isDark, onOpenEvidence, factId, relationId, expanded, setExpanded }) => {
  if (loading) {
    return (
      <div className={cn('flex items-center gap-2 py-3 text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
        Resolving provenance chain…
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn('flex items-center gap-2 py-2 text-xs', isDark ? 'text-amber-400' : 'text-amber-700')}>
        <AlertTriangle className="w-3.5 h-3.5" aria-hidden="true" />
        {error}
      </div>
    );
  }

  if (!data) return null;

  const columns = kindColumn.map((kind) => ({
    kind,
    nodes: kind === 'fact' ? data.facts : kind === 'evidence' ? data.evidence : data.documents,
  })).filter((c) => c.nodes.length > 0);

  const truncSuffix =
    data.metadata.truncated
      ? ` · truncated (${data.metadata.truncation_reasons.join('; ')}).`
      : ' · complete.';

  return (
    <div className="space-y-3">
      {/* Root conclusion */}
      <div
        className={cn(
          'p-2.5 rounded-lg border',
          isDark ? 'bg-slate-950/40 border-slate-800' : 'bg-white border-slate-200'
        )}
      >
        <div className="text-[10px] font-mono uppercase tracking-wider text-sky-500">
          {data.root_conclusion.relation_id ? 'Adjudicated Relation' : 'Fact'}
        </div>
        <div className={cn('font-mono text-xs mt-0.5', isDark ? 'text-slate-200' : 'text-slate-800')}>
          {data.root_conclusion.relation
            ? `${data.root_conclusion.relation} - ${data.root_conclusion.source_fact_id} → ${data.root_conclusion.target_fact_id}`
            : data.root_conclusion.fact_id}
        </div>
        {data.root_conclusion.explanation && (
          <p className={cn('text-[11px] leading-relaxed mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            {data.root_conclusion.explanation}
          </p>
        )}
        {data.root_conclusion.confidence != null && (
          <div className={cn('text-[10px] font-mono mt-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
            confidence {data.root_conclusion.confidence} · {data.root_conclusion.reason_code}
            {data.root_conclusion.decided_by ? ` · ${data.root_conclusion.decided_by}` : ''}
          </div>
        )}
      </div>

      {/* Columns */}
      <div className="space-y-3">
        {columns.map((col) => (
          <div key={col.kind}>
            <div
              className={cn(
                'text-[10px] font-mono uppercase tracking-wider mb-1 flex items-center gap-1.5',
                isDark ? 'text-slate-500' : 'text-slate-400'
              )}
            >
              <span className={NODE_STYLE[col.kind].text} aria-hidden="true">{NODE_STYLE[col.kind].glyph}</span>
              {NODE_STYLE[col.kind].label}
              <span className="opacity-60">({col.nodes.length})</span>
            </div>
            <div className="space-y-1.5">
              {col.nodes.map((node) => {
                const show = expanded[node.id] || col.kind !== 'evidence';
                return (
                  <div
                    key={node.id}
                    className={cn(
                      'rounded-lg border p-2',
                      isDark ? 'bg-slate-950/40 border-slate-800/80' : 'bg-white border-slate-200'
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className={cn('font-mono text-[11px] truncate', isDark ? 'text-slate-200' : 'text-slate-700')}>
                          {node.label}
                        </div>
                        <div className={cn('text-[10px] font-mono truncate mt-0.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
                          {node.subtitle}
                        </div>
                      </div>
                      {col.kind === 'evidence' && onOpenEvidence && (
                        <button
                          type="button"
                          onClick={() => {
                            const factIdVal = node.metadata?.fact_id;
                            if (factIdVal && onOpenEvidence) {
                              onOpenEvidence({ fact_id: factIdVal } as FactSummary);
                            }
                          }}
                          className={cn(
                            'shrink-0 px-2 py-1 rounded border text-[10px] font-mono flex items-center gap-1 transition-colors',
                            isDark
                              ? 'bg-sky-500/10 hover:bg-sky-500/20 text-sky-400 border-sky-500/20'
                              : 'bg-sky-50 hover:bg-sky-100 text-sky-700 border-sky-200'
                          )}
                          title="Open the source PDF at this evidence span"
                        >
                          <FileSearch className="w-3 h-3" aria-hidden="true" />
                          Source
                        </button>
                      )}
                    </div>
                    {col.kind === 'evidence' && node.metadata?.verbatim_quote && (
                      <details
                        className="mt-1.5"
                        open={!!expanded[node.id]}
                        onToggle={(e) => setExpanded((m) => ({ ...m, [node.id]: (e.target as HTMLDetailsElement).open }))}
                      >
                        <summary className={cn('cursor-pointer text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
                          {show ? 'hide quote' : 'verbatim quote'}
                        </summary>
                        {show && (
                          <p className={cn('text-[11px] italic leading-relaxed mt-1 border-l-2 pl-2', isDark ? 'border-slate-700 text-slate-300' : 'border-slate-300 text-slate-600')}>
                            “{node.metadata.verbatim_quote}”
                          </p>
                        )}
                      </details>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Truncation / projection notice - never silent */}
      <p className={cn('text-[10px] font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
        {data.metadata.node_count} nodes · {data.metadata.edge_count} edges · projected from{' '}
        {data.metadata.projection_of}{truncSuffix}
      </p>
    </div>
  );
};
