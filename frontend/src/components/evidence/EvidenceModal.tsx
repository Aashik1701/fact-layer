import React, { useEffect, useState } from 'react';
import { Modal } from '@/components/common/Modal';
import { PdfPageCanvas } from './PdfPageCanvas';
import { fetchFact } from '@/lib/api';
import { FactSummary, FactFull, EvidenceItem } from '@/types';
import { Quote, FileText, CheckCircle2, AlertCircle, Clock, MapPin } from 'lucide-react';
import { ConfidencePill } from '@/components/common/ConfidencePill';
import { formatIssuer, cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface EvidenceModalProps {
  isOpen: boolean;
  onClose: () => void;
  fact: FactSummary | FactFull | null;
  evidenceIndex?: number;
}

export const EvidenceModal: React.FC<EvidenceModalProps> = ({
  isOpen,
  onClose,
  fact,
  evidenceIndex = 0,
}) => {
  const { isDark } = useTheme();
  const [fullFact, setFullFact] = useState<FactFull | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [activeEvidenceIndex, setActiveEvidenceIndex] = useState<number>(evidenceIndex);

  useEffect(() => {
    setActiveEvidenceIndex(evidenceIndex);
  }, [evidenceIndex]);

  useEffect(() => {
    if (!isOpen || !fact) {
      setFullFact(null);
      return;
    }

    // If fact already has evidence array, use it
    if ('evidence' in fact && Array.isArray((fact as FactFull).evidence)) {
      setFullFact(fact as FactFull);
      return;
    }

    // Otherwise fetch the full fact with evidence
    setLoading(true);
    fetchFact(fact.fact_id)
      .then((data) => setFullFact(data))
      .catch((err) => console.error('Failed to load full fact evidence:', err))
      .finally(() => setLoading(false));
  }, [isOpen, fact]);

  if (!isOpen || !fact) return null;

  const evidenceList: EvidenceItem[] = fullFact?.evidence || [];
  const activeEvidence: EvidenceItem | null =
    evidenceList.length > 0 ? evidenceList[activeEvidenceIndex] || evidenceList[0] : null;

  const docId = activeEvidence?.doc_id || fact.doc_id || '';
  const pageNo = activeEvidence?.page || fact.page || 1;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-sky-500/10 border border-sky-500/20 text-sky-500">
            <FileText className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className={cn('text-base font-bold', isDark ? 'text-slate-100' : 'text-slate-900')}>
                Evidence Provenance
              </h3>
              <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-sky-500/10 text-sky-500 border border-sky-500/20">
                {fact.fact_id.slice(0, 10)}
              </span>
            </div>
            <p className={cn('text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Source PDF raster grounding with pixel-exact coordinate overlay
            </p>
          </div>
        </div>
      }
      maxWidth="6xl"
    >
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: PDF Page with Bounding Box Overlay */}
        <div className="lg:col-span-7 flex flex-col">
          {docId ? (
            <PdfPageCanvas
              docId={docId}
              page={pageNo}
              evidence={activeEvidence}
              filename={activeEvidence?.doc_id || fact.doc_id || 'document.pdf'}
            />
          ) : (
            <div className={cn(
              'p-8 text-center rounded-xl border text-xs',
              isDark ? 'bg-slate-900/50 border-slate-800 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500'
            )}>
              No document identifier attached to this fact.
            </div>
          )}
        </div>

        {/* Right Column: Grounded Quote & Fact Breakdown */}
        <div className="lg:col-span-5 flex flex-col space-y-5">
          {/* Verbatim Verified Quote Box */}
          <div
            className={cn(
              'p-4 rounded-xl border relative overflow-hidden',
              isDark ? 'bg-slate-800/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            )}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-500 uppercase tracking-wider">
                <Quote className="w-4 h-4" />
                Verbatim Extracted Quote
              </div>
              {activeEvidence?.verified ? (
                <div className="flex items-center gap-1 text-[11px] font-mono font-medium text-emerald-500 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                  <CheckCircle2 className="w-3 h-3" />
                  Span Verified
                </div>
              ) : (
                <div className="flex items-center gap-1 text-[11px] font-mono font-medium text-amber-500 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                  <AlertCircle className="w-3 h-3" />
                  Unverified Span
                </div>
              )}
            </div>

            {fact?.value_verification && (
              <div className="flex items-center justify-end mb-2 -mt-1">
                {fact.value_verification === 'verified' ? (
                  <div
                    title={fact.value_verification_reason || undefined}
                    className="flex items-center gap-1 text-[11px] font-mono font-medium text-emerald-500 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20"
                  >
                    <CheckCircle2 className="w-3 h-3" />
                    Value Verified
                  </div>
                ) : (
                  <div
                    title={fact.value_verification_reason || undefined}
                    className="flex items-center gap-1 text-[11px] font-mono font-medium text-amber-500 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20"
                  >
                    <AlertCircle className="w-3 h-3" />
                    Value Unverified
                  </div>
                )}
              </div>
            )}

            <blockquote
              className={cn(
                'p-3 rounded-lg border font-mono text-xs leading-relaxed italic border-l-4 border-l-amber-500',
                isDark
                  ? 'bg-slate-950/80 border-slate-800 text-slate-200'
                  : 'bg-white border-slate-200 text-slate-800'
              )}
            >
              "{activeEvidence?.verbatim_quote || (loading ? 'Loading evidence quote...' : 'No verbatim quote recorded.')}"
            </blockquote>

            {/* Character & Bbox Provenance */}
            <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div
                className={cn(
                  'p-2 rounded border',
                  isDark ? 'bg-slate-950/40 border-slate-800/80 text-slate-400' : 'bg-white border-slate-200 text-slate-600'
                )}
              >
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>CHARACTER SPAN</span>
                [{activeEvidence?.char_start ?? 0} : {activeEvidence?.char_end ?? 0}]
              </div>
              <div
                className={cn(
                  'p-2 rounded border',
                  isDark ? 'bg-slate-950/40 border-slate-800/80 text-slate-400' : 'bg-white border-slate-200 text-slate-600'
                )}
              >
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>EXTRACTOR</span>
                {activeEvidence?.extractor || 'llm'}
              </div>
            </div>

            {activeEvidence?.bbox && (
              <div
                className={cn(
                  'mt-2 p-2 rounded border text-[11px] font-mono',
                  isDark ? 'bg-slate-950/40 border-slate-800/80 text-slate-400' : 'bg-white border-slate-200 text-slate-600'
                )}
              >
                <span className={cn('block text-[10px]', isDark ? 'text-slate-500' : 'text-slate-400')}>BOUNDING BOX (PDF POINTS)</span>
                [{activeEvidence.bbox.map((v) => Math.round(v)).join(', ')}]
              </div>
            )}
          </div>

          {/* Fact Detail Card */}
          <div
            className={cn(
              'p-4 rounded-xl border space-y-3',
              isDark ? 'bg-slate-800/40 border-slate-800' : 'bg-slate-50 border-slate-200'
            )}
          >
            <div className={cn('flex items-center justify-between border-b pb-2', isDark ? 'border-slate-800' : 'border-slate-200')}>
              <span className={cn('text-xs font-semibold uppercase tracking-wider', isDark ? 'text-slate-300' : 'text-slate-700')}>
                Normalized Fact Card
              </span>
              <ConfidencePill confidence={fact.confidence} showIcon />
            </div>

            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Subject:</span>
                <span className="font-mono text-sky-500 font-semibold">{fact.subject}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Measure:</span>
                <span className={cn('font-mono font-semibold', isDark ? 'text-slate-200' : 'text-slate-900')}>{fact.measure}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Canonical Value:</span>
                <span className="font-mono text-emerald-500 font-bold text-sm">
                  {fact.value?.normalized} {fact.value?.unit || ''}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Raw Stated Value:</span>
                <span className={cn('font-mono', isDark ? 'text-slate-300' : 'text-slate-700')}>"{fact.value?.raw}"</span>
              </div>
              <div className="flex items-center justify-between">
                <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Modality:</span>
                <span
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] font-mono border',
                    isDark ? 'bg-slate-800 text-slate-300 border-slate-700' : 'bg-white text-slate-700 border-slate-200'
                  )}
                >
                  {fact.modality}
                </span>
              </div>
              {fact.qualifiers?.period && (
                <div className="flex items-center justify-between">
                  <span className={cn('flex items-center gap-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                    <Clock className="w-3 h-3 text-slate-400" />
                    Period:
                  </span>
                  <span className={cn('font-mono', isDark ? 'text-slate-300' : 'text-slate-700')}>
                    {fact.qualifiers.period.label ||
                      `${fact.qualifiers.period.start || ''} - ${fact.qualifiers.period.end || ''}`}
                  </span>
                </div>
              )}
              {fact.qualifiers?.issuer && (
                <div className="flex items-center justify-between">
                  <span className={isDark ? 'text-slate-400' : 'text-slate-500'}>Issuer:</span>
                  <span className={cn('font-medium', isDark ? 'text-slate-300' : 'text-slate-700')}>
                    {formatIssuer(fact.qualifiers.issuer)}
                  </span>
                </div>
              )}
              {fact.qualifiers?.scope && (
                <div className="flex items-center justify-between">
                  <span className={cn('flex items-center gap-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                    <MapPin className="w-3 h-3 text-slate-400" />
                    Scope:
                  </span>
                  <span className={cn('font-mono', isDark ? 'text-slate-300' : 'text-slate-700')}>{fact.qualifiers.scope}</span>
                </div>
              )}
            </div>
          </div>

          {/* Multiple evidence selector if fact has > 1 evidence */}
          {evidenceList.length > 1 && (
            <div
              className={cn(
                'p-3 rounded-lg border',
                isDark ? 'bg-slate-800/40 border-slate-800' : 'bg-slate-50 border-slate-200'
              )}
            >
              <span className={cn('text-[11px] font-semibold block mb-2', isDark ? 'text-slate-400' : 'text-slate-600')}>
                Available Evidence Anchors ({evidenceList.length}):
              </span>
              <div className="flex flex-wrap gap-2">
                {evidenceList.map((e, idx) => (
                  <button
                    key={idx}
                    onClick={() => setActiveEvidenceIndex(idx)}
                    className={cn(
                      'px-2.5 py-1 rounded text-xs font-mono transition-colors border',
                      activeEvidenceIndex === idx
                        ? 'bg-sky-500/20 text-sky-500 border-sky-500/40 font-semibold'
                        : isDark
                          ? 'bg-slate-800 text-slate-400 border-slate-700 hover:bg-slate-700 hover:text-slate-200'
                          : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-100 hover:text-slate-900'
                    )}
                  >
                    Anchor #{idx + 1} (Page {e.page})
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
};
