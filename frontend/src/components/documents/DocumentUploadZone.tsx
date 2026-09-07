import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ingestDocument, fetchJob } from '@/lib/api';
import { IngestJob, JobStage } from '@/types';
import { UploadCloud, CheckCircle2, AlertTriangle, AlertCircle, Loader2 } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface DocumentUploadZoneProps {
  onIngestSuccess: (result: IngestJob) => void;
}

// How often to poll GET /jobs/{id} while a job is in flight. Deliberately
// not aggressive — this is a demo-scale, single-user local app, not a
// system where sub-second staleness matters.
const POLL_INTERVAL_MS = 1200;

// A page refresh loses React state but not localStorage — remembering the
// active job id here means "handle browser refresh gracefully" doesn't
// require any server-side session; GET /jobs/{id} is stateless and safe to
// resume polling against after a reload.
const ACTIVE_JOB_STORAGE_KEY = 'fact_layer_active_ingest_job';

// Backend stages the job model can honestly report (fact_layer/jobs.py) —
// mapped to display text. There is deliberately no separate "Verifying"
// stage: span/value verification happen fact-by-fact, inline inside
// extraction, not as a discrete pass the backend could report a
// transition for (see Store.ingest()'s on_stage docstring) — one combined
// "Extracting & Verifying" step is more honest than inventing a boundary
// that doesn't exist.
const STAGE_LABELS: Record<JobStage, string> = {
  queued: 'Queued for processing...',
  parsing: 'Parsing PDF...',
  extracting: 'Extracting & Verifying Facts...',
  resolving: 'Resolving Entities & Measures...',
  adjudicating: 'Finding Relationships...',
  storing: 'Storing Results...',
  completed: 'Complete',
  failed: 'Failed',
};

const STAGE_ORDER: JobStage[] = [
  'queued', 'parsing', 'extracting', 'resolving', 'adjudicating', 'storing', 'completed',
];

function readActiveJob(): { jobId: string; filename: string } | null {
  try {
    const raw = localStorage.getItem(ACTIVE_JOB_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeActiveJob(jobId: string, filename: string) {
  try {
    localStorage.setItem(ACTIVE_JOB_STORAGE_KEY, JSON.stringify({ jobId, filename }));
  } catch {
    // localStorage can throw in private-browsing contexts — polling still
    // works within the current page load, refresh-recovery just won't.
  }
}

function clearActiveJob() {
  try {
    localStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export const DocumentUploadZone: React.FC<DocumentUploadZoneProps> = ({ onIngestSuccess }) => {
  const { isDark } = useTheme();
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const pollJob = useCallback((jobId: string) => {
    const tick = async () => {
      try {
        const latest = await fetchJob(jobId);
        setJob(latest);
        if (latest.status === 'completed' || latest.status === 'failed') {
          stopPolling();
          clearActiveJob();
          if (latest.status === 'completed') onIngestSuccess(latest);
          return;
        }
        pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err: any) {
        stopPolling();
        clearActiveJob();
        setErrorMessage(err.message || 'Lost track of ingestion job.');
      }
    };
    tick();
  }, [onIngestSuccess, stopPolling]);

  // Resume polling a job that was in flight when the page was last loaded
  // (e.g. the user refreshed mid-ingestion) — GET /jobs/{id} is stateless
  // and safe to poll again; if the job is gone (server restarted — jobs
  // are in-memory only, see README), the fetch failure clears local state
  // cleanly rather than polling forever.
  useEffect(() => {
    const active = readActiveJob();
    if (active) {
      setJob({
        job_id: active.jobId, filename: active.filename, status: 'processing', stage: 'queued',
        doc_id: null, already_ingested: false, error: null, result: null,
        created_at: '', updated_at: '',
      });
      pollJob(active.jobId);
    }
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setErrorMessage('Please upload a valid PDF document (.pdf).');
      return;
    }
    setIsSubmitting(true);
    setErrorMessage(null);
    setJob(null);
    try {
      const accepted = await ingestDocument(file);
      writeActiveJob(accepted.job_id, file.name);
      setJob({
        job_id: accepted.job_id, filename: file.name, status: accepted.status, stage: accepted.stage,
        doc_id: null, already_ingested: false, error: null, result: null,
        created_at: '', updated_at: '',
      });
      pollJob(accepted.job_id);
    } catch (err: any) {
      setErrorMessage(err.message || 'Failed to submit PDF for ingestion.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const isActive = isSubmitting || (job !== null && job.status !== 'completed' && job.status !== 'failed');
  const stageIndex = job ? STAGE_ORDER.indexOf(job.stage) : -1;

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
          {isActive ? <Loader2 className="w-7 h-7 animate-spin" /> : <UploadCloud className="w-7 h-7" />}
        </div>

        <h3 className={cn('text-sm font-semibold', isDark ? 'text-slate-200' : 'text-slate-800')}>
          {isActive
            ? (job ? STAGE_LABELS[job.stage] : 'Uploading...')
            : 'Drop a PDF document here or click to browse'}
        </h3>
        <p className={cn('text-xs max-w-md mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
          Extracts numerical &amp; semantic facts, verifies each one against verbatim source spans, normalizes units, and runs the comparability gate.
        </p>

        {/* Stage progress — discrete steps, never a fabricated percentage */}
        {isActive && job && (
          <div className="mt-4 flex items-center gap-1.5 flex-wrap justify-center max-w-md">
            {STAGE_ORDER.slice(0, -1).map((s, i) => (
              <div
                key={s}
                className={cn(
                  'px-2 py-1 rounded-full text-[10px] font-mono border transition-colors',
                  i < stageIndex
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500'
                    : i === stageIndex
                    ? 'bg-sky-500/10 border-sky-500/30 text-sky-500'
                    : isDark
                    ? 'bg-slate-800/60 border-slate-700 text-slate-500'
                    : 'bg-slate-100 border-slate-200 text-slate-400'
                )}
              >
                {STAGE_LABELS[s].replace(/\.\.\.$/, '').replace(' for processing', '')}
              </div>
            ))}
          </div>
        )}

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

      {/* Job Result */}
      {job && job.status === 'completed' && (
        <div
          className={cn(
            'p-4 rounded-xl border flex items-start gap-3 text-xs animate-in fade-in slide-in-from-top-2 duration-200',
            job.already_ingested
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-500'
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-500'
          )}
        >
          {job.already_ingested ? (
            <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
          ) : (
            <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
          )}
          <div className="space-y-1">
            <div className="font-semibold text-sm font-mono">
              {job.already_ingested
                ? 'Document Already Ingested (No Double Counting)'
                : 'Document Ingested & Indexed Successfully'}
            </div>
            <p className="leading-relaxed">
              {`File "${job.filename}" parsed with ${job.result?.counts?.facts_verified ?? 0} facts verified and ${job.result?.new_relations?.length ?? 0} new relations formed.`}
            </p>
            <div className={cn('text-[11px] font-mono pt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Doc ID: {job.doc_id}
            </div>
          </div>
        </div>
      )}

      {job && job.status === 'failed' && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-500 flex items-center gap-3 text-xs">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span>{job.error || 'Ingestion failed.'}</span>
        </div>
      )}

      {/* Validation Error (rejected before a job was even created) */}
      {errorMessage && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-500 flex items-center gap-3 text-xs">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  );
};
