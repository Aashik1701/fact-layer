import React, { useState, useEffect, useRef } from 'react';
import { getPageImageUrl } from '@/lib/api';
import { ZoomIn, ZoomOut, RotateCcw, AlertCircle, FileText } from 'lucide-react';
import { EvidenceItem } from '@/types';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface PdfPageCanvasProps {
  docId: string;
  page: number;
  evidence?: EvidenceItem | null;
  filename?: string;
}

export const PdfPageCanvas: React.FC<PdfPageCanvasProps> = ({
  docId,
  page,
  evidence,
  filename,
}) => {
  const { isDark } = useTheme();
  // Defaults to the whole page fitted inside the viewport (computed on
  // image load, below) rather than a fixed 100% — a raster page routinely
  // exceeds the viewport at native size, so opening evidence at 100% zoom
  // showed only whatever the auto-scroll centered on: effectively a crop
  // around the anchor, not the page. Fitting the whole page in view by
  // default means the evidence box is seen in its real context every time,
  // with zoom controls still available to inspect it more closely.
  const [zoom, setZoom] = useState<number>(1);
  const [fitZoom, setFitZoom] = useState<number>(1);
  // The rendered PDF page's own pixel size at the fixed DPI the backend
  // rasterized it at — needed to size the page wrapper in real pixels (see
  // handleImageLoad) rather than via a CSS transform, which visually scales
  // the image but leaves its LAYOUT footprint (and therefore the
  // viewport's scrollable area and centering) at the untransformed, native
  // size. At zoom < 1 (the new fit-to-page default) that mismatch left a
  // page shrunk to, say, 40% of its size sitting inside a scroll area still
  // reserving 100% of it — masquerading as dead space below the page.
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [hasError, setHasError] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const viewportRef = useRef<HTMLDivElement>(null);
  const anchorHighlightRef = useRef<HTMLDivElement>(null);

  const imageUrl = getPageImageUrl(docId, page);

  useEffect(() => {
    setLoading(true);
  }, [docId, page]);

  // A reviewer who has zoomed in past the fitted view can still lose track
  // of where the evidence anchor sits — keep it in view as a safety net,
  // but only nudge the minimum distance ('nearest') rather than forcing it
  // to the center every time, since at the default fitted zoom the whole
  // page (and the anchor) is already visible and shouldn't jump around.
  useEffect(() => {
    if (loading) return;
    anchorHighlightRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'auto' });
  }, [loading, evidence?.doc_id, evidence?.page, evidence?.bbox]);

  const handleZoomIn = () => setZoom((prev) => Math.min(prev + 0.25, 3));
  const handleZoomOut = () => setZoom((prev) => Math.max(prev - 0.25, 0.1));
  const handleResetZoom = () => setZoom(fitZoom);

  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    const viewport = viewportRef.current;
    if (viewport && img.naturalWidth && img.naturalHeight) {
      setNaturalSize({ width: img.naturalWidth, height: img.naturalHeight });
      const PADDING = 32; // matches the viewport's p-4 (16px) on each side
      const availableWidth = viewport.clientWidth - PADDING;
      const availableHeight = viewport.clientHeight - PADDING;
      const fit = Math.min(availableWidth / img.naturalWidth, availableHeight / img.naturalHeight, 1);
      const clamped = Math.max(0.1, Math.min(fit, 3));
      setFitZoom(clamped);
      setZoom(clamped);
    }
    setLoading(false);
  };

  // Compute bbox overlay percentages
  const bboxAsPct = (bbox: [number, number, number, number]): React.CSSProperties => {
    const [x0, top, x1, bottom] = bbox;
    const pw = evidence!.page_width!;
    const ph = evidence!.page_height!;
    const leftPct = Math.max(0, (x0 / pw) * 100);
    const topPct = Math.max(0, (top / ph) * 100);
    const widthPct = Math.min(100 - leftPct, ((x1 - x0) / pw) * 100);
    const heightPct = Math.min(100 - topPct, ((bottom - top) / ph) * 100);
    return { left: `${leftPct}%`, top: `${topPct}%`, width: `${widthPct}%`, height: `${heightPct}%` };
  };

  const canOverlay = !!(evidence && evidence.page_width && evidence.page_height);
  const bboxStyle = canOverlay && evidence!.bbox ? bboxAsPct(evidence!.bbox) : null;
  // A8: when this fact's value was confidently attributed to a specific
  // table cell, the stored cell_bbox is a second, independent region —
  // drawn only when it's genuinely present, never synthesized from row/
  // column labels (there is no stored row-bbox or column-bbox to draw).
  const cellBboxStyle = canOverlay && evidence!.cell_bbox ? bboxAsPct(evidence!.cell_bbox) : null;

  // The span anchor (verbatim quote match) and the target cell (table
  // structure attribution) are computed independently — a span-verified
  // quote can be a bare, short number with no row/column words in it, so
  // bbox_for_span() legitimately lands on a different occurrence of that
  // same number than the one the table's own cell-grid identifies. Both
  // are real, honest coordinates; when they land in genuinely different
  // page regions (checked against the two already-stored boxes only —
  // never a new coordinate), say so, rather than leaving a reviewer to
  // wonder why two boxes appear apart with no explanation.
  const boxesDisjoint = !!(
    evidence?.bbox && evidence?.cell_bbox &&
    (evidence.bbox[2] < evidence.cell_bbox[0] || evidence.cell_bbox[2] < evidence.bbox[0] ||
     evidence.bbox[3] < evidence.cell_bbox[1] || evidence.cell_bbox[3] < evidence.bbox[1])
  );

  return (
    <div
      className={cn(
        'flex flex-col h-full rounded-xl overflow-hidden border',
        isDark ? 'bg-slate-950 border-slate-800' : 'bg-slate-100 border-slate-200'
      )}
    >
      {/* Viewer Controls Toolbar */}
      <div
        className={cn(
          'flex items-center justify-between px-4 py-2.5 border-b text-xs',
          isDark ? 'bg-slate-900 border-slate-800 text-slate-300' : 'bg-white border-slate-200 text-slate-700'
        )}
      >
        <div className="flex items-center gap-2 truncate">
          <FileText className="w-4 h-4 text-sky-500 shrink-0" />
          <span className={cn('font-mono truncate', isDark ? 'text-slate-200' : 'text-slate-800')}>{filename || docId}</span>
          <span
            className={cn(
              'px-1.5 py-0.5 rounded font-mono',
              isDark ? 'bg-slate-800 text-slate-400' : 'bg-slate-100 text-slate-600 border border-slate-200'
            )}
          >
            Page {page}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          <button
            onClick={handleZoomOut}
            className={cn(
              'p-1.5 rounded transition-colors',
              isDark
                ? 'hover:bg-slate-800 text-slate-400 hover:text-slate-200'
                : 'hover:bg-slate-100 text-slate-500 hover:text-slate-800'
            )}
            title="Zoom Out"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className={cn('font-mono text-xs px-1 min-w-[3rem] text-center', isDark ? 'text-slate-400' : 'text-slate-600')}>
            {Math.round(zoom * 100)}%
          </span>
          <button
            onClick={handleZoomIn}
            className={cn(
              'p-1.5 rounded transition-colors',
              isDark
                ? 'hover:bg-slate-800 text-slate-400 hover:text-slate-200'
                : 'hover:bg-slate-100 text-slate-500 hover:text-slate-800'
            )}
            title="Zoom In"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={handleResetZoom}
            className={cn(
              'p-1.5 rounded transition-colors ml-1',
              isDark
                ? 'hover:bg-slate-800 text-slate-400 hover:text-slate-200'
                : 'hover:bg-slate-100 text-slate-500 hover:text-slate-800'
            )}
            title="Fit Whole Page"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Honest degraded-evidence note — never silently drop the fact that no
          exact coordinates are available; page-level grounding is still real
          evidence, just not cell-precise. */}
      {evidence && !evidence.bbox && (
        <div
          className={cn(
            'flex items-center gap-2 px-4 py-2 text-[11px] border-b',
            isDark
              ? 'bg-amber-500/5 border-slate-800 text-amber-400'
              : 'bg-amber-50 border-slate-200 text-amber-700'
          )}
        >
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>Page-level evidence only — no exact bounding box was recorded for this span.</span>
        </div>
      )}

      {boxesDisjoint && (
        <div
          className={cn(
            'flex items-center gap-2 px-4 py-2 text-[11px] border-b',
            isDark
              ? 'bg-sky-500/5 border-slate-800 text-sky-400'
              : 'bg-sky-50 border-slate-200 text-sky-700'
          )}
        >
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span>
            The quoted text (amber) and the attributed table cell (blue) are in different positions on this page —
            both independently support the same value.
          </span>
        </div>
      )}

      {/* Canvas Viewport */}
      <div
        ref={viewportRef}
        className={cn(
          'flex-1 overflow-auto p-4 flex items-center justify-center min-h-[420px] max-h-[80vh] relative',
          isDark ? 'bg-slate-950/60' : 'bg-slate-50'
        )}
      >
        {loading && (
          <div
            className={cn(
              'absolute inset-0 flex items-center justify-center z-10',
              isDark ? 'bg-slate-950/70' : 'bg-white/70'
            )}
          >
            <div className={cn('flex flex-col items-center gap-2 text-xs', isDark ? 'text-slate-400' : 'text-slate-600')}>
              <div className="w-6 h-6 border-2 border-sky-500 border-t-transparent rounded-full animate-spin" />
              <span>Rendering PDF page pixels...</span>
            </div>
          </div>
        )}

        {hasError ? (
          <div className={cn('flex flex-col items-center justify-center p-8 text-center gap-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
            <AlertCircle className="w-8 h-8 text-amber-500 mb-1" />
            <span className={cn('font-semibold', isDark ? 'text-slate-200' : 'text-slate-800')}>Unable to load PDF page</span>
            <span className={cn('text-xs max-w-sm', isDark ? 'text-slate-500' : 'text-slate-400')}>
              The PDF raster could not be rendered from the cache or source file.
            </span>
          </div>
        ) : (
          <div
            className="relative shadow-2xl rounded transition-[width,height] duration-150"
            style={
              naturalSize
                ? { width: naturalSize.width * zoom, height: naturalSize.height * zoom }
                : undefined
            }
          >
            {/* The PDF Page Image — sized in real pixels (not CSS transform)
                so the wrapper's own layout box always matches what's on
                screen; see naturalSize's comment above for why that matters
                at fit-to-page zoom levels below 100%. */}
            <img
              src={imageUrl}
              alt={`Page ${page} of ${docId}`}
              className={cn(
                'block w-full h-full rounded border bg-white',
                isDark ? 'border-slate-700' : 'border-slate-300'
              )}
              onLoad={handleImageLoad}
              onError={() => {
                setLoading(false);
                setHasError(true);
              }}
            />

            {/* Table Cell Highlight Overlay (A8) — drawn first/underneath so
                the span anchor's own highlight stays visually primary */}
            {cellBboxStyle && (
              <div
                style={cellBboxStyle}
                className="absolute pointer-events-none rounded border-2 border-sky-400 bg-sky-400/15 ring-2 ring-sky-400/20"
                title="Table cell this value was attributed to"
              >
                <span className="absolute -bottom-5 left-0 px-1.5 py-0.5 rounded bg-sky-500 text-[10px] font-mono font-bold text-slate-950 shadow whitespace-nowrap">
                  TARGET CELL
                </span>
              </div>
            )}

            {/* Bounding Box Highlight Overlay */}
            {bboxStyle && (
              <div
                ref={anchorHighlightRef}
                style={bboxStyle}
                className="absolute pointer-events-none rounded border-2 border-amber-400 bg-amber-400/25 ring-4 ring-amber-400/20 shadow-lg animate-pulse"
                title="Exact evidence provenance coordinates on page"
              >
                <span className="absolute -top-5 left-0 px-1.5 py-0.5 rounded bg-amber-500 text-[10px] font-mono font-bold text-slate-950 shadow">
                  EVIDENCE ANCHOR
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
