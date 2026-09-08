import React, { useCallback, useMemo, useRef, useState } from 'react';
import { GraphEdge, GraphNeighborhood, GraphNode, GraphNodeType } from '@/types';
import { cn } from '@/lib/utils';

// --------------------------------------------------------------------------
// Hand-rolled SVG canvas. No graph library.
//
// Justification (the project applies the same test to every dependency):
// the view is a BOUNDED neighbourhood - tens of nodes, never the whole
// corpus - and the shape being drawn is a provenance chain, not an arbitrary
// network. A deterministic layered layout (entity → fact → evidence →
// document, left to right) reads that chain directly, where a force-directed
// library would produce the spider web this view exists to avoid, ship
// 50-400 kB, and fight the keyboard/table accessibility requirements. Pan,
// zoom, fit and selection are a viewBox transform and a click handler.
// --------------------------------------------------------------------------

const COLUMN_ORDER: GraphNodeType[] = ['entity', 'fact', 'evidence', 'document'];

const NODE_W = 168;
const NODE_H = 46;
const COL_GAP = 236;
const ROW_GAP = 62;
const PADDING = 40;

export const NODE_STYLE: Record<
  GraphNodeType,
  { fill: string; stroke: string; text: string; glyph: string; label: string }
> = {
  entity: { fill: 'fill-sky-500/10', stroke: 'stroke-sky-500', text: 'text-sky-500', glyph: '◆', label: 'Entity' },
  fact: { fill: 'fill-indigo-500/10', stroke: 'stroke-indigo-500', text: 'text-indigo-500', glyph: '●', label: 'Fact' },
  evidence: { fill: 'fill-emerald-500/10', stroke: 'stroke-emerald-500', text: 'text-emerald-500', glyph: '▣', label: 'Evidence' },
  document: { fill: 'fill-amber-500/10', stroke: 'stroke-amber-500', text: 'text-amber-500', glyph: '▤', label: 'Document' },
};

const STRUCTURAL = new Set(['HAS_FACT', 'SUPPORTED_BY', 'LOCATED_IN']);

export const isStructural = (edge: GraphEdge) => STRUCTURAL.has(edge.type);

export interface Positioned extends GraphNode {
  x: number;
  y: number;
}

/** Deterministic layered layout: column by node type, row by stable id order.
 *  Same payload always yields the same picture - no physics, no jitter, no
 *  re-settling between two identical requests. */
export function layout(nodes: GraphNode[]): { positioned: Positioned[]; width: number; height: number } {
  const columns = new Map<GraphNodeType, GraphNode[]>();
  COLUMN_ORDER.forEach((t) => columns.set(t, []));
  nodes.forEach((n) => columns.get(n.type)?.push(n));
  columns.forEach((list) => list.sort((a, b) => (a.depth - b.depth) || a.id.localeCompare(b.id)));

  const tallest = Math.max(1, ...COLUMN_ORDER.map((t) => columns.get(t)!.length));
  const height = PADDING * 2 + tallest * ROW_GAP;
  const activeCols = COLUMN_ORDER.filter((t) => columns.get(t)!.length > 0);
  const width = PADDING * 2 + Math.max(1, activeCols.length) * COL_GAP;

  const positioned: Positioned[] = [];
  activeCols.forEach((type, colIndex) => {
    const list = columns.get(type)!;
    const colHeight = list.length * ROW_GAP;
    const yStart = (height - colHeight) / 2 + ROW_GAP / 2;
    list.forEach((node, rowIndex) => {
      positioned.push({
        ...node,
        x: PADDING + colIndex * COL_GAP + NODE_W / 2,
        y: yStart + rowIndex * ROW_GAP,
      });
    });
  });
  return { positioned, width, height };
}

interface Props {
  data: GraphNeighborhood;
  selectedId: string | null;
  onSelectNode: (node: GraphNode) => void;
  onSelectEdge: (edge: GraphEdge) => void;
  visibleNodeTypes: Set<GraphNodeType>;
  visibleRelationTypes: Set<string>;
  isDark: boolean;
}

export const KnowledgeGraphCanvas: React.FC<Props> = ({
  data, selectedId, onSelectNode, onSelectEdge,
  visibleNodeTypes, visibleRelationTypes, isDark,
}) => {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);

  const visibleNodes = useMemo(
    () => data.nodes.filter((n) => visibleNodeTypes.has(n.type)),
    [data.nodes, visibleNodeTypes]
  );
  const { positioned, width, height } = useMemo(() => layout(visibleNodes), [visibleNodes]);
  const posById = useMemo(() => new Map(positioned.map((p) => [p.id, p])), [positioned]);

  const visibleEdges = useMemo(
    () =>
      data.edges.filter((e) => {
        if (!posById.has(e.source) || !posById.has(e.target)) return false;
        if (isStructural(e)) return true;
        return visibleRelationTypes.has(e.type);
      }),
    [data.edges, posById, visibleRelationTypes]
  );

  const fit = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    dragRef.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setPan({ x: d.panX + (e.clientX - d.x), y: d.panY + (e.clientY - d.y) });
  };
  const onPointerUp = () => { dragRef.current = null; };

  const onWheel = (e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return;   // don't hijack ordinary page scroll
    e.preventDefault();
    setZoom((z) => Math.min(2.5, Math.max(0.4, z - e.deltaY * 0.002)));
  };

  return (
    <div className="relative h-full">
      {/* Zoom / fit controls */}
      <div className="absolute top-2 right-2 z-10 flex gap-1">
        {[
          { label: '−', title: 'Zoom out', fn: () => setZoom((z) => Math.max(0.4, z - 0.2)) },
          { label: '+', title: 'Zoom in', fn: () => setZoom((z) => Math.min(2.5, z + 0.2)) },
          { label: 'Fit', title: 'Fit graph to view', fn: fit },
        ].map((b) => (
          <button
            key={b.title}
            type="button"
            onClick={b.fn}
            title={b.title}
            aria-label={b.title}
            className={cn(
              'px-2 py-1 rounded border text-[11px] font-mono',
              isDark ? 'bg-slate-900 border-slate-700 text-slate-300' : 'bg-white border-slate-300 text-slate-600'
            )}
          >
            {b.label}
          </button>
        ))}
      </div>

      <svg
        className={cn('w-full h-full touch-none', dragRef.current ? 'cursor-grabbing' : 'cursor-grab')}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
        role="application"
        aria-label="Knowledge graph neighbourhood. A textual list of the same nodes and edges follows below."
      >
        <defs>
          <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className={isDark ? 'fill-slate-600' : 'fill-slate-400'} />
          </marker>
          <marker id="kg-arrow-rel" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-rose-500" />
          </marker>
        </defs>

        <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
          {/* Edges first so nodes paint over them */}
          {visibleEdges.map((edge) => {
            const a = posById.get(edge.source)!;
            const b = posById.get(edge.target)!;
            const structural = isStructural(edge);
            const sameColumn = a.type === b.type;
            const x1 = a.x + NODE_W / 2;
            const x2 = b.x - NODE_W / 2;
            const path = sameColumn
              // Relation edges join two facts in the same column: bow them
              // out to the right so they never overlap the node boxes.
              ? `M ${a.x + NODE_W / 2} ${a.y} C ${a.x + NODE_W / 2 + 70} ${a.y}, ${b.x + NODE_W / 2 + 70} ${b.y}, ${b.x + NODE_W / 2} ${b.y}`
              : `M ${x1} ${a.y} C ${x1 + 50} ${a.y}, ${x2 - 50} ${b.y}, ${x2} ${b.y}`;
            const midX = sameColumn ? a.x + NODE_W / 2 + 46 : (x1 + x2) / 2;
            const midY = (a.y + b.y) / 2;
            return (
              <g key={edge.id} className="cursor-pointer" onClick={(e) => { e.stopPropagation(); onSelectEdge(edge); }}>
                <path
                  d={path}
                  fill="none"
                  className={cn(
                    structural
                      ? isDark ? 'stroke-slate-700' : 'stroke-slate-300'
                      : 'stroke-rose-500/70'
                  )}
                  strokeWidth={structural ? 1.2 : 1.8}
                  strokeDasharray={structural ? undefined : '4 3'}
                  markerEnd={structural ? 'url(#kg-arrow)' : 'url(#kg-arrow-rel)'}
                />
                {/* Relation labels are always drawn: direction alone must
                    never be the only signal of what a relation means. */}
                {!structural && (
                  <text
                    x={midX} y={midY - 4} textAnchor="middle"
                    className="fill-rose-500 text-[8px] font-mono pointer-events-none"
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            );
          })}

          {positioned.map((node) => {
            const style = NODE_STYLE[node.type];
            const selected = node.id === selectedId;
            const isRoot = node.id === data.root.id;
            return (
              <g
                key={node.id}
                transform={`translate(${node.x - NODE_W / 2} ${node.y - NODE_H / 2})`}
                className="cursor-pointer focus:outline-none"
                tabIndex={0}
                role="button"
                aria-label={`${style.label}: ${node.label}. ${node.subtitle}`}
                aria-pressed={selected}
                onClick={(e) => { e.stopPropagation(); onSelectNode(node); }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelectNode(node); }
                }}
              >
                <rect
                  width={NODE_W} height={NODE_H} rx={8}
                  className={cn(style.fill, style.stroke, isDark ? 'fill-opacity-100' : '')}
                  strokeWidth={selected ? 2.5 : isRoot ? 2 : 1}
                  strokeDasharray={isRoot && !selected ? '5 3' : undefined}
                />
                <text x={10} y={18} className={cn('text-[10px] font-mono', style.text)}>
                  {style.glyph} {style.label}
                </text>
                <text
                  x={10} y={31}
                  className={cn('text-[11px] font-medium', isDark ? 'fill-slate-100' : 'fill-slate-800')}
                >
                  {node.label.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}
                </text>
                <text
                  x={10} y={41}
                  className={cn('text-[9px] font-mono', isDark ? 'fill-slate-400' : 'fill-slate-500')}
                >
                  {node.subtitle.length > 28 ? `${node.subtitle.slice(0, 27)}…` : node.subtitle}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
};
