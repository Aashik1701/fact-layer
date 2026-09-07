import React, { useState, useEffect } from 'react';
import { fetchRejectedFacts } from '@/lib/api';
import { RejectedFact } from '@/types';
import { LoadingSkeleton } from '@/components/common/LoadingSkeleton';
import { EmptyState } from '@/components/common/EmptyState';
import { ShieldAlert, FileX, Search, RefreshCw, AlertTriangle } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

export const RejectedFactsPage: React.FC = () => {
  const { isDark } = useTheme();
  const [rejectedFacts, setRejectedFacts] = useState<RejectedFact[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedReason, setSelectedReason] = useState<string>('ALL');

  const loadRejectedFacts = async () => {
    setLoading(true);
    setError(null);
    try {
      const reasonParam = selectedReason === 'ALL' ? undefined : selectedReason;
      const res = await fetchRejectedFacts(250, reasonParam);
      setRejectedFacts(res.rejected_facts);
      setTotal(res.total);
    } catch (err) {
      console.error('Failed to load rejected facts:', err);
      setError('Unable to load rejected facts log.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadRejectedFacts(); }, [selectedReason]);

  const filteredFacts = rejectedFacts.filter((rf) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      (rf.raw_fact?.verbatim_quote && rf.raw_fact.verbatim_quote.toLowerCase().includes(query)) ||
      (rf.reason && rf.reason.toLowerCase().includes(query)) ||
      (rf.doc_id && rf.doc_id.toLowerCase().includes(query)) ||
      (rf.raw_fact?.subject_raw && rf.raw_fact.subject_raw.toLowerCase().includes(query))
    );
  });

  const inputCls = cn(
    'w-full pl-9 pr-3 py-2 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60',
    isDark
      ? 'bg-slate-950 border-slate-800 text-slate-200 placeholder-slate-500'
      : 'bg-white border-slate-200 text-slate-800 placeholder-slate-400'
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-xl font-bold flex items-center gap-2', isDark ? 'text-slate-100' : 'text-slate-900')}>
            <ShieldAlert className="w-5 h-5 text-rose-500" />
            Quality Control &amp; Rejected Facts
          </h2>
          <p className={cn('text-xs mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
            Candidate extractions the pipeline refused to admit into the trusted store ({total} recorded in rejected_facts.jsonl)
          </p>
        </div>

        <button
          onClick={loadRejectedFacts}
          className={cn(
            'self-start sm:self-auto px-3 py-1.5 rounded-lg text-xs font-mono flex items-center gap-1.5 transition-colors border',
            isDark
              ? 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700'
              : 'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200'
          )}
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh Log
        </button>
      </div>

      {/* Info Banner */}
      <div
        className={cn(
          'p-4 rounded-xl border flex items-start gap-3 text-xs',
          isDark
            ? 'bg-slate-900 border-slate-800 text-slate-300'
            : 'bg-amber-50 border-amber-200 text-slate-600'
        )}
      >
        <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
        <p className="leading-relaxed">
          <span className={cn('font-semibold', isDark ? 'text-slate-100' : 'text-slate-800')}>
            The system refused to guess:
          </span>{' '}
          Every fact accepted into the knowledge layer must pass exact character span verification against the PDF, and its numeric value must be independently confirmed against that same verified quote. A candidate with a quote that isn't actually in the source text, a missing subject/measure, an unparseable value, or a value that disagrees with its own cited evidence is rejected here rather than admitted — logged for full auditability, not hidden.
        </p>
      </div>

      {/* Search Toolbar */}
      <div
        className={cn(
          'p-4 rounded-xl border flex flex-col sm:flex-row gap-3',
          isDark ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
        )}
      >
        <div className="flex-1 relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search candidate quote, rejection reason, or entity..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className={inputCls}
          />
        </div>

        <div className="sm:w-64">
          <select
            value={selectedReason}
            onChange={(e) => setSelectedReason(e.target.value)}
            className={cn(
              'w-full py-2 px-3 rounded-lg text-xs border focus:outline-none focus:border-sky-500/60 font-mono',
              isDark
                ? 'bg-slate-950 border-slate-800 text-slate-200'
                : 'bg-white border-slate-200 text-slate-700'
            )}
          >
            <option value="ALL">All Rejection Reasons</option>
            <option value="quote_not_found">quote_not_found</option>
            <option value="no_subject">no_subject</option>
            <option value="no_measure">no_measure</option>
            <option value="unparseable_value">unparseable_value</option>
            <option value="value_mismatch">value_mismatch</option>
          </select>
        </div>
      </div>

      {/* Table or States */}
      {loading ? (
        <LoadingSkeleton rows={6} />
      ) : error ? (
        <div className="p-6 text-center bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs">
          {error}
        </div>
      ) : filteredFacts.length === 0 ? (
        <EmptyState
          icon={FileX}
          title="No Rejected Facts"
          description="No ungrounded facts match the selected filters."
          action={{ label: 'Reset Filters', onClick: () => { setSelectedReason('ALL'); setSearchQuery(''); } }}
        />
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
                <th className="py-3 px-4 w-1/2">Rejected Candidate Quote</th>
                <th className="py-3 px-4">Failure Reason</th>
                <th className="py-3 px-4">Page</th>
                <th className="py-3 px-4">Entity / Value</th>
                <th className="py-3 px-4">Document ID</th>
              </tr>
            </thead>
            <tbody
              className={cn(
                'font-sans',
                isDark ? 'divide-y divide-slate-800/60' : 'divide-y divide-slate-100'
              )}
            >
              {filteredFacts.map((rf, idx) => (
                <tr
                  key={idx}
                  className={cn('transition-colors', isDark ? 'hover:bg-slate-800/40' : 'hover:bg-rose-50/30')}
                >
                  <td className="py-3.5 px-4">
                    <blockquote
                      className={cn(
                        'font-mono text-xs italic p-2.5 rounded-lg border border-l-2 border-l-rose-500 line-clamp-3',
                        isDark
                          ? 'text-rose-300/90 bg-slate-950/60 border-rose-500/20'
                          : 'text-rose-600 bg-rose-50/60 border-rose-200'
                      )}
                    >
                      "{rf.raw_fact?.verbatim_quote || rf.detail || 'no quote proposed'}"
                    </blockquote>
                  </td>

                  <td className="py-3.5 px-4 font-mono text-[11px]">
                    <span className="px-2 py-0.5 rounded bg-rose-500/10 text-rose-500 border border-rose-500/20 whitespace-nowrap">
                      {rf.reason || 'unknown'}
                    </span>
                  </td>

                  <td className={cn('py-3.5 px-4 font-mono', isDark ? 'text-slate-400' : 'text-slate-500')}>
                    {rf.page_no != null ? `Page ${rf.page_no}` : '—'}
                  </td>

                  <td className="py-3.5 px-4">
                    <div className="flex flex-col font-mono text-[11px]">
                      <span className={isDark ? 'text-slate-300' : 'text-slate-700'}>
                        {rf.raw_fact?.subject_raw
                          ? `${rf.raw_fact.subject_raw}::${rf.raw_fact.measure_raw || ''}`
                          : '—'}
                      </span>
                      {rf.raw_fact?.value_raw && (
                        <span className={isDark ? 'text-slate-500 text-[10px]' : 'text-slate-400 text-[10px]'}>
                          Raw: {rf.raw_fact.value_raw}
                        </span>
                      )}
                    </div>
                  </td>

                  <td
                    className={cn(
                      'py-3.5 px-4 font-mono text-[11px] truncate max-w-[120px]',
                      isDark ? 'text-slate-500' : 'text-slate-400'
                    )}
                    title={rf.doc_id || ''}
                  >
                    {rf.doc_id ? rf.doc_id.slice(0, 10) + '...' : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
