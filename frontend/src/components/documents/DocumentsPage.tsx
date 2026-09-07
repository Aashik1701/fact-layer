import React, { useState, useEffect } from 'react';
import { fetchDocuments } from '@/lib/api';
import { DocumentInfo, IngestResponse } from '@/types';
import { DocumentUploadZone } from './DocumentUploadZone';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { FileText, RefreshCw, CheckCircle2, AlertTriangle } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface DocumentsPageProps {
  onNavigateToFacts?: (docId?: string) => void;
}

export const DocumentsPage: React.FC<DocumentsPageProps> = ({ onNavigateToFacts }) => {
  const { isDark } = useTheme();
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadDocuments = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchDocuments();
      setDocuments(res.documents);
    } catch (err) {
      console.error('Failed to load documents:', err);
      setError('Unable to load documents from store.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadDocuments(); }, []);

  const handleIngestSuccess = (_result: IngestResponse) => { loadDocuments(); };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <FileText className="w-5 h-5 text-sky-500" />
            Documents Explorer &amp; Ingest
          </h2>
          <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Corpus documents participating in cross-document fact reasoning ({documents.length} loaded)
          </p>
        </div>

        <button
          onClick={loadDocuments}
          className={cn(
            'self-start sm:self-auto px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border',
            isDark
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
          )}
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh Corpus
        </button>
      </div>

      {/* Upload Zone */}
      <div className="space-y-3">
        <h3 className={cn('text-xs font-semibold uppercase tracking-wider', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Ingest New PDF Document
        </h3>
        <DocumentUploadZone onIngestSuccess={handleIngestSuccess} />
      </div>

      {/* Documents Table */}
      <div className="space-y-3">
        <h3 className={cn('text-xs font-semibold uppercase tracking-wider', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Ingested Documents in Knowledge Layer
        </h3>

        {loading ? (
          <LoadingSkeleton rows={5} />
        ) : error ? (
          <div className="p-6 text-center bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs">
            {error}
          </div>
        ) : (
          <div
            className={cn(
              'overflow-x-auto rounded-xl border',
              isDark ? 'border-slate-800 bg-slate-900/60' : 'border-slate-200 bg-white shadow-sm'
            )}
          >
            <table className="w-full text-xs text-left">
              <thead
                className={cn(
                  'font-mono text-[11px] border-b uppercase tracking-wider',
                  isDark
                    ? 'bg-slate-900 text-slate-400 border-slate-800'
                    : 'bg-slate-50 text-slate-500 border-slate-200'
                )}
              >
                <tr>
                  <th className="py-3 px-4">Document File Name</th>
                  <th className="py-3 px-4">Document ID</th>
                  <th className="py-3 px-4">Extracted Facts</th>
                  <th className="py-3 px-4">Pages</th>
                  <th className="py-3 px-4">Table Strategy</th>
                  <th className="py-3 px-4">Health</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody
                className={cn(
                  'font-sans',
                  isDark ? 'divide-y divide-slate-800/60' : 'divide-y divide-slate-100'
                )}
              >
                {documents.map((doc) => (
                  <tr
                    key={doc.doc_id}
                    className={cn(
                      'transition-colors',
                      isDark ? 'hover:bg-slate-800/40' : 'hover:bg-sky-50/30'
                    )}
                  >
                    <td className="py-3.5 px-4">
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4 text-sky-500 shrink-0" />
                        <span
                          className={cn(
                            'font-medium truncate max-w-sm',
                            isDark ? 'text-slate-200' : 'text-slate-800'
                          )}
                        >
                          {doc.filename}
                        </span>
                      </div>
                    </td>

                    <td
                      className={cn(
                        'py-3.5 px-4 font-mono text-[11px]',
                        isDark ? 'text-slate-400' : 'text-slate-500'
                      )}
                    >
                      {doc.doc_id.slice(0, 12)}...
                    </td>

                    <td className="py-3.5 px-4 font-mono font-semibold text-emerald-500">
                      {doc.fact_count} facts
                    </td>

                    <td className={cn('py-3.5 px-4 font-mono', isDark ? 'text-slate-300' : 'text-slate-600')}>
                      {doc.n_pages ?? '—'}
                    </td>

                    <td className={cn('py-3.5 px-4 font-mono text-[11px]', isDark ? 'text-slate-400' : 'text-slate-500')}>
                      <span
                        className={cn(
                          'px-2 py-0.5 rounded border',
                          isDark ? 'bg-slate-800 border-slate-700' : 'bg-slate-100 border-slate-200'
                        )}
                      >
                        {doc.table_strategy || 'hybrid'}
                      </span>
                    </td>

                    <td className="py-3.5 px-4">
                      {doc.diagnostics ? (
                        (() => {
                          const d = doc.diagnostics!;
                          const clean = d.warnings.length === 0;
                          const tooltip = [
                            `${d.total_pages} pages`,
                            `${d.text_pages} text pages`,
                            `${d.image_only_pages} image-only pages`,
                            `${d.sparse_pages} sparse pages`,
                            `${d.tables_detected} tables detected`,
                            ...(d.warnings.length ? ['Warnings:', ...d.warnings.map((w) => `• ${w}`)] : []),
                          ].join('\n');
                          return (
                            <span
                              title={tooltip}
                              className={cn(
                                'inline-flex items-center gap-1 px-2 py-0.5 rounded border font-mono text-[11px] cursor-help',
                                clean
                                  ? 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20'
                                  : 'text-amber-500 bg-amber-500/10 border-amber-500/20'
                              )}
                            >
                              {clean ? <CheckCircle2 className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
                              {clean ? 'Clean' : `${d.warnings.length} warning${d.warnings.length > 1 ? 's' : ''}`}
                            </span>
                          );
                        })()
                      ) : (
                        <span className={cn('font-mono text-[11px]', isDark ? 'text-slate-500' : 'text-slate-400')}>—</span>
                      )}
                    </td>

                    <td className="py-3.5 px-4 text-right">
                      <button
                        onClick={() => onNavigateToFacts && onNavigateToFacts(doc.doc_id)}
                        className="px-3 py-1 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-500 border border-sky-500/20 font-mono text-xs transition-colors"
                      >
                        Explore Facts
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
