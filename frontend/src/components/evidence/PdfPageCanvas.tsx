import React, { useState } from 'react';
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
  const [zoom, setZoom] = useState<number>(1);
  const [hasError, setHasError] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  const imageUrl = getPageImageUrl(docId, page);

  const handleZoomIn = () => setZoom((prev) => Math.min(prev + 0.25, 2.5));
  const handleZoomOut = () => setZoom((prev) => Math.max(prev - 0.25, 0.5));
  const handleResetZoom = () => setZoom(1);

  // Compute bbox overlay percentages
  let bboxStyle: React.CSSProperties | null = null;
  if (evidence && evidence.bbox && evidence.page_width && evidence.page_height) {
    const [x0, top, x1, bottom] = evidence.bbox;
    const pw = evidence.page_width;
    const ph = evidence.page_height;

    // Add slight padding around the bounding box
    const leftPct = Math.max(0, (x0 / pw) * 100);
    const topPct = Math.max(0, (top / ph) * 100);
    const widthPct = Math.min(100 - leftPct, ((x1 - x0) / pw) * 100);
    const heightPct = Math.min(100 - topPct, ((bottom - top) / ph) * 100);

    bboxStyle = {
      left: `${leftPct}%`,
      top: `${topPct}%`,
      width: `${widthPct}%`,
      height: `${heightPct}%`,
    };
  }

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
            title="Reset Zoom"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Canvas Viewport */}
      <div
        className={cn(
          'flex-1 overflow-auto p-4 flex items-center justify-center min-h-[420px] max-h-[650px] relative',
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
            className="relative transition-transform duration-150 origin-top shadow-2xl rounded"
            style={{ transform: `scale(${zoom})`, transformOrigin: 'top center' }}
          >
            {/* The PDF Page Image */}
            <img
              src={imageUrl}
              alt={`Page ${page} of ${docId}`}
              className={cn(
                'max-w-none rounded border bg-white',
                isDark ? 'border-slate-700' : 'border-slate-300'
              )}
              onLoad={() => setLoading(false)}
              onError={() => {
                setLoading(false);
                setHasError(true);
              }}
            />

            {/* Bounding Box Highlight Overlay */}
            {bboxStyle && (
              <div
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
