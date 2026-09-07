import React, { useState, useRef } from 'react';
import { ingestDocument } from '@/lib/api';
import { IngestResponse } from '@/types';
import { UploadCloud, CheckCircle2, AlertTriangle, AlertCircle, Loader2 } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface DocumentUploadZoneProps {
  onIngestSuccess: (result: IngestResponse) => void;
}

export const DocumentUploadZone: React.FC<DocumentUploadZoneProps> = ({ onIngestSuccess }) => {
  const { isDark } = useTheme();
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [uploadResult, setUploadResult] = useState<IngestResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setErrorMessage('Please upload a valid PDF document (.pdf).');
      return;
    }
    setIsUploading(true);
    setErrorMessage(null);
    setUploadResult(null);
    try {
      const res = await ingestDocument(file);
      setUploadResult(res);
      onIngestSuccess(res);
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to ingest and extract PDF.');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Drop Zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => { e.preventDefault(); setIsDragging(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => fileInputRef.current?.click()}
        className={cn(
          'p-8 rounded-xl border-2 border-dashed transition-all cursor-pointer flex flex-col items-center justify-center text-center',
          isDragging
            ? 'border-sky-400 bg-sky-500/10 scale-[1.01]'
            : isDark
            ? 'border-slate-700 bg-slate-900/40 hover:bg-slate-900/70 hover:border-slate-600'
            : 'border-slate-200 bg-slate-50 hover:bg-slate-100 hover:border-slate-300'
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />

        <div className="w-14 h-14 rounded-2xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-500 mb-4 shadow-lg shadow-sky-500/10">
          {isUploading ? <Loader2 className="w-7 h-7 animate-spin" /> : <UploadCloud className="w-7 h-7" />}
        </div>

        <h3 className={cn('text-sm font-semibold', isDark ? 'text-slate-200' : 'text-slate-800')}>
          {isUploading ? 'Extracting & Grounding PDF Document...' : 'Drop a PDF document here or click to browse'}
        </h3>
        <p className={cn('text-xs max-w-md mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Extracts numerical &amp; semantic facts, executes OCR grounding to verbatim spans, normalizes units, and runs the comparability gate.
        </p>

        <div
          className={cn(
            'mt-3 inline-flex items-center gap-2 px-3 py-1 rounded-full border text-[11px] font-mono',
            isDark
              ? 'bg-slate-800/80 border-slate-700 text-slate-400'
              : 'bg-slate-100 border-slate-200 text-slate-500'
          )}
        >
          <span>Supported: PDF documents</span>
          <span>•</span>
          <span className="text-emerald-500">Deterministic Span Verification</span>
        </div>
      </div>

      {/* Upload Result */}
      {uploadResult && (
        <div
          className={cn(
            'p-4 rounded-xl border flex items-start gap-3 text-xs animate-in fade-in slide-in-from-top-2 duration-200',
            uploadResult.status === 'already_indexed'
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-500'
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500'
          )}
        >
          {uploadResult.status === 'already_indexed' ? (
            <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
          ) : (
            <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
          )}
          <div className="space-y-1">
            <div className="font-semibold text-sm font-mono">
              {uploadResult.status === 'already_indexed'
                ? 'Document Already Ingested (No Double Counting)'
                : 'Document Ingested & Indexed Successfully'}
            </div>
            <p className="leading-relaxed">
              {uploadResult.message ||
                `File "${uploadResult.filename}" parsed with ${uploadResult.facts_extracted} facts extracted and ${uploadResult.relations_formed ?? 0} relations formed.`}
            </p>
            <div className={cn('text-[11px] font-mono pt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Doc ID: {uploadResult.doc_id}
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-500 flex items-center gap-3 text-xs">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  );
};
