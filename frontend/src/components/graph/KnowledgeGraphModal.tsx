import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Modal } from '@/components/common/Modal';
import { ErrorBoundary } from '@/components/common/ErrorBoundary';
import {
  GraphEdge, GraphNeighborhood, GraphNode, GraphNodeType, FactSummary, GraphSearchResult,
} from '@/types';
import { fetchGraphNeighborhood, fetchFact, searchGraph } from '@/lib/api';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { KnowledgeGraphCanvas, NODE_STYLE, isStructural } from './KnowledgeGraphCanvas';
import { ComparabilityInvestigator } from '@/components/facts/ComparabilityInvestigator';
import { Loader2, Network, FileText, Scale, CornerDownRight, Search } from 'lucide-react';

// --------------------------------------------------------------------------
// The investigation surface over the graph projection.
//
// Everything shown here is derived from GET /graph/... - a read-only view of
// Store.facts / Store.relations / Store.get_evidence(). The graph reports
// relationships; it never decides them, and a retrieval candidate is never
// drawn as one.
// --------------------------------------------------------------------------

const ALL_NODE_TYPES: GraphNodeType[] = ['entity', 'fact', 'evidence', 'document'];

interface Props {
  isOpen: boolean;
  onClose: () => void;
  rootType: GraphNodeType;
  rootId: string;
  /** Evidence roots are addressed as (fact_id, index). */
  rootIndex?: number;
  title?: string;
  onOpenEvidence?: (fact: FactSummary) => void;
}

const Row: React.FC<{ label: string; value: React.ReactNode; isDark: boolean }> = ({
  label, value, isDark,
}) => (
  <div className="flex items-baseline justify-between gap-3 text-[11px] leading-5">
    <span className={cn('font-mono shrink-0', isDark ? 'text-slate-500' : 'text-slate-400')}>{label}</span>
    <span className={cn('font-mono text-right break-words', isDark ? 'text-slate-200' : 'text-slate-700')}>
      {value ?? '-'}
    </span>
  </div>
);

const SectionLabel: React.FC<{ children: React.ReactNode; isDark: boolean }> = ({ children, isDark }) => (
  <h4 className={cn('text-[10px] font-semibold uppercase tracking-wider pb-1 mb-1.5 border-b',
    isDark ? 'text-slate-400 border-slate-800' : 'text-slate-500 border-slate-200')}>
    {children}
  </h4>
);

export const KnowledgeGraphModal: React.FC<Props> = ({
  isOpen, onClose, rootType, rootId, rootIndex, title, onOpenEvidence,
}) => {
  const { isDark } = useTheme();
  const [data, setData] = useState<GraphNeighborhood | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [depth, setDepth] = useState(2);
  const [root, setRoot] = useState<{ type: GraphNodeType; id: string; index?: number }>({
    type: rootType, id: rootId, index: rootIndex,
  });
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [investigatePair, setInvestigatePair] = useState<{ a: FactSummary; bId: string } | null>(null);
  const [nodeTypes, setNodeTypes] = useState<Set<GraphNodeType>>(new Set(ALL_NODE_TYPES));
  const [relationTypes, setRelationTypes] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<GraphSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchTouched, setSearchTouched] = useState(false);

  // Debounced node search (spec §13B): find any node in the projection and
  // re-centre the graph on it. Search is a READ-ONLY lookup - selecting a
  // result never fabricates an edge; it only re-roots the walk.
  useEffect(() => {
    const q = searchQuery.trim();
    if (!isOpen || !q) { setSearchResults(null); setSearching(false); return; }
    setSearching(true);
    setSearchTouched(true);
    const handle = window.setTimeout(async () => {
      try {
        const results = await searchGraph(q, 10);
        setSearchResults(results);
      } catch (err) {
        console.error('Graph search failed:', err);
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => window.clearTimeout(handle);
  }, [searchQuery, isOpen]);

  useEffect(() => {
    setRoot({ type: rootType, id: rootId, index: rootIndex });
  }, [rootType, rootId, rootIndex]);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelectedEdge(null);
    setInvestigatePair(null);
    fetchGraphNeighborhood(root.type, root.id, { depth, index: root.index })
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setSelectedNode(res.root);
        setRelationTypes(
          new Set(res.edges.filter((e) => !isStructural(e)).map((e) => e.type))
        );
      })
      .catch((err) => {
        console.error('Graph load failed:', err);
        if (!cancelled) setError('Could not load this neighbourhood.');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isOpen, root, depth]);

  const availableRelationTypes = useMemo(
    () => Array.from(new Set((data?.edges ?? []).filter((e) => !isStructural(e)).map((e) => e.type))).sort(),
    [data]
  );

  const toggle = <T,>(set: Set<T>, value: T, apply: (s: Set<T>) => void) => {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    apply(next);
  };

  const recenter = useCallback((node: GraphNode) => {
    if (node.type === 'evidence') {
      const [factId, idx] = node.id.replace(/^evidence:/, '').split('#');
      setRoot({ type: 'evidence', id: factId, index: Number(idx) || 0 });
    } else {
      setRoot({ type: node.type, id: node.source_id });
    }
  }, []);

  const jumpToResult = useCallback((result: GraphSearchResult) => {
    if (result.type === 'evidence') {
      const [factId, idx] = result.id.replace(/^evidence:/, '').split('#');
      setRoot({ type: 'evidence', id: factId, index: Number(idx) || 0 });
    } else {
      setRoot({ type: result.type, id: result.id });
    }
    setSearchQuery('');
    setSearchResults(null);
  }, []);

  const openComparability = useCallback(async (node: GraphNode) => {
    if (!data || node.type !== 'fact') return;
    const rootFactId = data.root.type === 'fact' ? data.root.source_id : null;
    if (!rootFactId || rootFactId === node.source_id) return;
    try {
      const a = await fetchFact(rootFactId);
      setInvestigatePair({ a, bId: node.source_id });
    } catch (err) {
      console.error('Could not load fact for comparability:', err);
    }
  }, [data]);

  const nodesById = useMemo(
    () => new Map((data?.nodes ?? []).map((n) => [n.id, n])),
    [data]
  );

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Knowledge Graph" maxWidth="5xl">
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <p className={cn('text-[12px] font-medium flex items-center gap-1.5', isDark ? 'text-slate-200' : 'text-slate-700')}>
              <Network className="w-3.5 h-3.5 text-sky-500" aria-hidden="true" />
              {title || data?.root.label || 'Neighbourhood'}
            </p>
            <p className={cn('text-[10px] font-mono mt-0.5', isDark ? 'text-slate-500' : 'text-slate-400')}>
              A read-only projection of the fact layer. Relationship edges exist only because the
              adjudicator recorded them.
            </p>
          </div>
          <label className={cn('text-[11px] font-mono flex items-center gap-1.5', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Depth
            <select
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className={cn('rounded border px-1.5 py-0.5 text-[11px]',
                isDark ? 'bg-slate-900 border-slate-700 text-slate-200' : 'bg-white border-slate-300 text-slate-700')}
            >
              {[0, 1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        </div>

        {/* Legend + filters */}
        <div className={cn('flex flex-wrap gap-x-4 gap-y-2 p-2 rounded-lg border text-[10px]',
          isDark ? 'bg-slate-900/50 border-slate-800' : 'bg-slate-50 border-slate-200')}>
          <div className="relative flex-1 min-w-[180px]">
            <Search className="w-3 h-3 text-slate-400 absolute left-2 top-1/2 -translate-y-1/2" aria-hidden="true" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Jump to a node in the projection (entity, fact, evidence, document)…"
              className={cn(
                'w-full pl-7 pr-2 py-1.5 rounded border text-[11px] focus:outline-none focus:border-sky-500/60',
                isDark
                  ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
                  : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
              )}
            />
            {searching && (
              <Loader2 className="w-3 h-3 animate-spin text-sky-500 absolute right-2 top-1/2 -translate-y-1/2" aria-hidden="true" />
            )}
            {!searching && searchTouched && searchQuery.trim() && searchResults && searchResults.length === 0 && (
              <span className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 text-[10px]">no matches</span>
            )}
            {!searching && searchQuery.trim() && searchResults && searchResults.length > 0 && (
              <div
                className={cn(
                  'absolute z-20 left-0 right-0 top-full mt-1 rounded-lg border shadow-xl max-h-56 overflow-y-auto',
                  isDark ? 'bg-slate-900 border-slate-700' : 'bg-white border-slate-200'
                )}
              >
                {searchResults.map((r) => (
                  <button
                    key={`${r.type}:${r.id}`}
                    type="button"
                    onClick={() => jumpToResult(r)}
                    className={cn(
                      'w-full text-left px-2.5 py-2 flex items-center gap-2 hover:bg-sky-500/10',
                      isDark ? 'text-slate-200' : 'text-slate-700'
                    )}
                  >
                    <span className={NODE_STYLE[r.type].text} aria-hidden="true">{NODE_STYLE[r.type].glyph}</span>
                    <span className="min-w-0">
                      <span className="block font-mono text-[11px] truncate">{r.label}</span>
                      <span className={cn('block text-[10px] font-mono truncate', isDark ? 'text-slate-500' : 'text-slate-400')}>{r.subtitle}</span>
                    </span>
                    <span className={cn('ml-auto shrink-0 text-[9px] font-mono uppercase', NODE_STYLE[r.type].text)}>
                      {NODE_STYLE[r.type].label}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <fieldset className="flex items-center gap-2 flex-wrap">
            <legend className="sr-only">Node types</legend>
            {ALL_NODE_TYPES.map((t) => (
              <label key={t} className="flex items-center gap-1 cursor-pointer font-mono">
                <input
                  type="checkbox" checked={nodeTypes.has(t)}
                  onChange={() => toggle(nodeTypes, t, setNodeTypes)}
                  className="w-3 h-3"
                />
                <span className={NODE_STYLE[t].text} aria-hidden="true">{NODE_STYLE[t].glyph}</span>
                <span className={isDark ? 'text-slate-300' : 'text-slate-600'}>{NODE_STYLE[t].label}</span>
              </label>
            ))}
          </fieldset>
          {availableRelationTypes.length > 0 && (
            <fieldset className="flex items-center gap-2 flex-wrap">
              <legend className="sr-only">Relationship types</legend>
              {availableRelationTypes.map((t) => (
                <label key={t} className="flex items-center gap-1 cursor-pointer font-mono">
                  <input
                    type="checkbox" checked={relationTypes.has(t)}
                    onChange={() => toggle(relationTypes, t, setRelationTypes)}
                    className="w-3 h-3"
                  />
                  <span className="text-rose-500">{t.replace(/_/g, ' ')}</span>
                </label>
              ))}
            </fieldset>
          )}
        </div>

        {loading ? (
          <div className={cn('flex items-center gap-2 py-10 justify-center text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
            Projecting neighbourhood…
          </div>
        ) : error ? (
          <p className={cn('text-xs py-6 text-center', isDark ? 'text-slate-500' : 'text-slate-400')} role="alert">
            {error} This is a display failure, not a statement about the knowledge base.
          </p>
        ) : data ? (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-3">
              {/* Canvas */}
              <div className={cn('rounded-lg border h-[420px] overflow-hidden',
                isDark ? 'bg-slate-950/40 border-slate-800' : 'bg-white border-slate-200')}>
                {data.nodes.length > 0 ? (
                  <ErrorBoundary compact label="The graph couldn't be rendered." key={data.nodes.map((n) => n.id).join(',')}>
                    <KnowledgeGraphCanvas
                      data={data}
                      selectedId={selectedNode?.id ?? null}
                      onSelectNode={(n) => { setSelectedNode(n); setSelectedEdge(null); }}
                      onSelectEdge={(e) => { setSelectedEdge(e); setSelectedNode(null); }}
                      visibleNodeTypes={nodeTypes}
                      visibleRelationTypes={relationTypes}
                      isDark={isDark}
                    />
                  </ErrorBoundary>
                ) : (
                  <p className={cn('p-6 text-xs text-center', isDark ? 'text-slate-500' : 'text-slate-400')}>
                    No nodes in this neighbourhood.
                  </p>
                )}
              </div>

              {/* Inspector */}
              <aside
                className={cn('rounded-lg border p-3 space-y-3 max-h-[420px] overflow-y-auto',
                  isDark ? 'bg-slate-900/50 border-slate-800' : 'bg-slate-50 border-slate-200')}
                aria-label="Selected node or relationship inspector"
              >
                {selectedEdge ? (
                  isStructural(selectedEdge) ? (
                    <>
                      <SectionLabel isDark={isDark}>Provenance link</SectionLabel>
                      <Row label="type" value={selectedEdge.label} isDark={isDark} />
                      <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                        A structural link in the provenance chain, not a semantic relationship.
                      </p>
                    </>
                  ) : (
                    <>
                      <SectionLabel isDark={isDark}>Relationship</SectionLabel>
                      <p className="text-[13px] font-semibold text-rose-500">{selectedEdge.label}</p>
                      <Row label="confidence" value={selectedEdge.metadata.confidence} isDark={isDark} />
                      <Row label="reason" value={selectedEdge.metadata.reason_code} isDark={isDark} />
                      <Row label="decided by" value={selectedEdge.metadata.decided_by} isDark={isDark} />
                      <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-300' : 'text-slate-600')}>
                        {selectedEdge.metadata.explanation}
                      </p>
                      <div className="space-y-1 pt-1">
                        {(['source', 'target'] as const).map((side) => {
                          const id = selectedEdge.metadata[`${side}_fact_id`];
                          const node = nodesById.get(`fact:${id}`);
                          if (!node) return null;
                          return (
                            <button
                              key={side}
                              type="button"
                              onClick={() => { setSelectedNode(node); setSelectedEdge(null); }}
                              className={cn('w-full text-left px-2 py-1.5 rounded border text-[11px]',
                                isDark ? 'bg-slate-950/40 border-slate-800 text-slate-300' : 'bg-white border-slate-200 text-slate-600')}
                            >
                              <CornerDownRight className="w-3 h-3 inline mr-1" aria-hidden="true" />
                              {node.label} · {node.subtitle}
                            </button>
                          );
                        })}
                      </div>
                      <p className={cn('text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                        Recorded by the adjudicator after the comparability gate passed. The graph did not
                        infer it.
                      </p>
                    </>
                  )
                ) : selectedNode ? (
                  <>
                    <SectionLabel isDark={isDark}>{NODE_STYLE[selectedNode.type].label}</SectionLabel>
                    <p className={cn('text-[12px] font-medium', isDark ? 'text-slate-100' : 'text-slate-800')}>
                      {selectedNode.label}
                    </p>
                    <p className={cn('text-[11px] font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      {selectedNode.subtitle}
                    </p>

                    {selectedNode.type === 'fact' && (
                      <div className="space-y-0.5 pt-1">
                        <Row label="entity" value={selectedNode.metadata.subject} isDark={isDark} />
                        <Row label="measure" value={selectedNode.metadata.measure} isDark={isDark} />
                        <Row label="value" value={selectedNode.metadata.value} isDark={isDark} />
                        <Row label="period" value={selectedNode.metadata.period} isDark={isDark} />
                        <Row label="scope" value={selectedNode.metadata.scope} isDark={isDark} />
                        <Row label="issuer" value={selectedNode.metadata.issuer} isDark={isDark} />
                        <Row label="verification" value={selectedNode.metadata.value_verification} isDark={isDark} />
                      </div>
                    )}

                    {selectedNode.type === 'evidence' && (
                      <div className="space-y-1 pt-1">
                        <Row label="document" value={selectedNode.metadata.filename} isDark={isDark} />
                        <Row label="page" value={selectedNode.metadata.page} isDark={isDark} />
                        <Row label="span verified" value={String(selectedNode.metadata.verified)} isDark={isDark} />
                        <p className={cn('text-[11px] italic p-2 rounded border',
                          isDark ? 'bg-slate-950/40 border-slate-800 text-slate-300' : 'bg-white border-slate-200 text-slate-600')}>
                          “{selectedNode.metadata.verbatim_quote}”
                        </p>
                        <Row
                          label="location"
                          value={`chars ${selectedNode.metadata.char_start}-${selectedNode.metadata.char_end}`}
                          isDark={isDark}
                        />
                      </div>
                    )}

                    {selectedNode.type === 'entity' && (
                      <div className="space-y-0.5 pt-1">
                        <Row label="facts" value={selectedNode.metadata.fact_count} isDark={isDark} />
                        <Row label="documents" value={selectedNode.metadata.document_count} isDark={isDark} />
                        <p className={cn('text-[10px] pt-1', isDark ? 'text-slate-500' : 'text-slate-400')}>
                          Facts share this entity only because entity resolution already assigned them the
                          same canonical subject. The graph never merges entities itself.
                        </p>
                      </div>
                    )}

                    {selectedNode.type === 'document' && (
                      <div className="space-y-0.5 pt-1">
                        <Row label="facts" value={selectedNode.metadata.fact_count} isDark={isDark} />
                      </div>
                    )}

                    <div className="space-y-1 pt-2">
                      <button
                        type="button"
                        onClick={() => recenter(selectedNode)}
                        className={cn('w-full px-2 py-1.5 rounded border text-[11px] font-medium',
                          isDark ? 'bg-slate-950/40 border-slate-700 text-slate-300' : 'bg-white border-slate-300 text-slate-600')}
                      >
                        Re-centre graph here
                      </button>
                      {selectedNode.type === 'evidence' && onOpenEvidence && (
                        <button
                          type="button"
                          onClick={async () => {
                            try {
                              const fact = await fetchFact(selectedNode.metadata.fact_id);
                              onOpenEvidence(fact);
                            } catch (err) { console.error(err); }
                          }}
                          className={cn('w-full px-2 py-1.5 rounded border text-[11px] font-medium flex items-center justify-center gap-1.5',
                            isDark ? 'bg-slate-950/40 border-slate-700 text-slate-300' : 'bg-white border-slate-300 text-slate-600')}
                        >
                          <FileText className="w-3 h-3" aria-hidden="true" />
                          Open source page
                        </button>
                      )}
                      {selectedNode.type === 'fact' && data.root.type === 'fact'
                        && data.root.source_id !== selectedNode.source_id && (
                        <button
                          type="button"
                          onClick={() => openComparability(selectedNode)}
                          className={cn('w-full px-2 py-1.5 rounded border text-[11px] font-medium flex items-center justify-center gap-1.5',
                            isDark ? 'bg-slate-950/40 border-slate-700 text-slate-300' : 'bg-white border-slate-300 text-slate-600')}
                        >
                          <Scale className="w-3 h-3" aria-hidden="true" />
                          Investigate comparability
                        </button>
                      )}
                    </div>
                  </>
                ) : (
                  <p className={cn('text-[11px]', isDark ? 'text-slate-500' : 'text-slate-400')}>
                    Select a node or a relationship.
                  </p>
                )}
              </aside>
            </div>

            {/* Truncation notice - never silent */}
            <p className={cn('text-[10px] font-mono px-1',
              data.metadata.truncated
                ? (isDark ? 'text-amber-400' : 'text-amber-700')
                : (isDark ? 'text-slate-500' : 'text-slate-400'))}>
              Showing a {data.metadata.depth}-hop neighbourhood · {data.metadata.node_count} nodes ·{' '}
              {data.metadata.edge_count} edges
              {data.metadata.truncated
                ? ` · truncated (${data.metadata.truncation_reasons.join('; ')}). Re-centre on a node to continue.`
                : ' · complete at this depth.'}
            </p>

            {/* Accessible fallback: the same neighbourhood as text */}
            <details className="group">
              <summary className={cn('cursor-pointer text-[11px] font-mono px-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                Text view of this neighbourhood
              </summary>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-[11px] border-collapse">
                  <caption className="sr-only">Nodes and relationships in the current neighbourhood</caption>
                  <thead>
                    <tr className={isDark ? 'text-slate-500' : 'text-slate-400'}>
                      <th scope="col" className="text-left font-mono font-normal py-1 pr-2">Type</th>
                      <th scope="col" className="text-left font-mono font-normal py-1 pr-2">Label</th>
                      <th scope="col" className="text-left font-mono font-normal py-1">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.nodes.map((n) => (
                      <tr key={n.id} className={cn('border-t', isDark ? 'border-slate-800/70' : 'border-slate-200')}>
                        <th scope="row" className={cn('text-left font-normal font-mono py-1 pr-2', NODE_STYLE[n.type].text)}>
                          {NODE_STYLE[n.type].label}
                        </th>
                        <td className={cn('py-1 pr-2', isDark ? 'text-slate-300' : 'text-slate-600')}>{n.label}</td>
                        <td className={cn('py-1 font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>{n.subtitle}</td>
                      </tr>
                    ))}
                    {data.edges.filter((e) => !isStructural(e)).map((e) => (
                      <tr key={e.id} className={cn('border-t', isDark ? 'border-slate-800/70' : 'border-slate-200')}>
                        <th scope="row" className="text-left font-normal font-mono py-1 pr-2 text-rose-500">Relationship</th>
                        <td className={cn('py-1 pr-2', isDark ? 'text-slate-300' : 'text-slate-600')}>{e.label}</td>
                        <td className={cn('py-1 font-mono', isDark ? 'text-slate-500' : 'text-slate-400')}>
                          {e.metadata.reason_code} · confidence {e.metadata.confidence}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>

            {investigatePair && (
              <div className="pt-1">
                <ComparabilityInvestigator
                  factA={investigatePair.a}
                  factBId={investigatePair.bId}
                  provenance="Pair selected in the knowledge graph"
                />
              </div>
            )}
          </>
        ) : null}
      </div>
    </Modal>
  );
};
