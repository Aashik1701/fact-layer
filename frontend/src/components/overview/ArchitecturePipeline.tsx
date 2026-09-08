import React, { useEffect, useState } from 'react';
import { FileSearch, Layers, GitCompare, ShieldCheck, Radar } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { fetchRetrievalStats } from '@/lib/api';
import { RetrievalStats } from '@/types';

export const ArchitecturePipeline: React.FC = () => {
  const { isDark } = useTheme();
  const [retrieval, setRetrieval] = useState<RetrievalStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRetrievalStats()
      .then((r) => { if (!cancelled) setRetrieval(r); })
      .catch(() => { if (!cancelled) setRetrieval(null); });
    return () => { cancelled = true; };
  }, []);

  const steps = [
    {
      num: '01', title: 'Grounding & Span Verification', badge: 'Pixel Verification',
      desc: 'Anchor candidate quotes to exact PDF character coordinates; reject hallucinated text.',
      icon: FileSearch, color: isDark ? 'text-sky-400' : 'text-sky-600',
    },
    {
      num: '02', title: 'Normalization', badge: 'Canonical Schema',
      desc: 'Standardize units, currencies, fiscal periods, and assign modality taxonomy.',
      icon: Layers, color: isDark ? 'text-indigo-400' : 'text-indigo-600',
    },
    {
      num: '03', title: 'Retrieval + Scale', badge: 'Candidate Generation',
      desc: 'Blocking, lexical (BM25) and semantic (embedding) retrieval narrow which fact pairs ever reach the gate - it never decides comparability itself.',
      icon: Radar, color: isDark ? 'text-fuchsia-400' : 'text-fuchsia-600',
    },
    {
      num: '04', title: 'Comparability Gate', badge: 'Qualifier Guard',
      desc: 'Deterministic rules verify qualifier alignment (period, scope, modality) before comparison.',
      icon: GitCompare, color: isDark ? 'text-amber-400' : 'text-amber-600',
    },
    {
      num: '05', title: 'Adjudication', badge: 'Audit Provenance',
      desc: 'Classify 5 relational verdicts; penalize true contradictions by halving confidence to 0.50.',
      icon: ShieldCheck, color: isDark ? 'text-emerald-400' : 'text-emerald-600',
    },
  ];

  return (
    <div
      className={cn(
        'p-6 rounded-xl border space-y-5',
        isDark ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200 shadow-sm'
      )}
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <span className={cn('text-[10px] font-mono uppercase tracking-wider font-semibold',
            isDark ? 'text-sky-400' : 'text-sky-600')}>
            System Mechanics
          </span>
          <h3 className={cn('text-base font-bold', isDark ? 'text-slate-100' : 'text-slate-900')}>
            The Deterministic Fact Verification Pipeline
          </h3>
        </div>
        <div className={cn(
          'px-2.5 py-1 rounded-full border text-xs font-mono',
          isDark
            ? 'bg-sky-500/10 border-sky-500/20 text-sky-400'
            : 'bg-sky-50 border-sky-200 text-sky-700'
        )}>
          Comparability Before Comparison
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {steps.map((step, idx) => {
          const Icon = step.icon;
          return (
            <div
              key={idx}
              className={cn(
                'p-4 rounded-xl border space-y-2 flex flex-col',
                isDark ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
              )}
            >
              <div className="flex items-center justify-between">
                <span className={cn('font-mono text-xs font-bold', isDark ? 'text-slate-500' : 'text-slate-400')}>
                  {step.num}
                </span>
                <span className={cn(
                  'text-[10px] font-mono px-2 py-0.5 rounded border',
                  isDark ? 'bg-slate-800 text-slate-300 border-slate-700' : 'bg-white text-slate-500 border-slate-200'
                )}>
                  {step.badge}
                </span>
              </div>
              <div className="space-y-1 my-2">
                <div className="flex items-center gap-2">
                  <Icon className={cn('w-4 h-4', step.color)} />
                  <h4 className={cn('text-xs font-bold', isDark ? 'text-slate-200' : 'text-slate-800')}>
                    {step.title}
                  </h4>
                </div>
                <p className={cn('text-[11px] leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {step.desc}
                </p>
              </div>
            </div>
          );
        })}
      </div>

      {retrieval && (
        <div
          className={cn(
            'flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 rounded-lg border text-[11px] font-mono',
            isDark ? 'bg-slate-950/40 border-slate-800 text-slate-400' : 'bg-slate-50 border-slate-200 text-slate-500'
          )}
        >
          <span className={cn('font-semibold', isDark ? 'text-fuchsia-400' : 'text-fuchsia-600')}>
            Retrieval index -
          </span>
          <span>{retrieval.indexed_facts.toLocaleString()} facts indexed</span>
          <span>·</span>
          <span>{retrieval.embedding_model} ({retrieval.embedding_dimension}d)</span>
          <span>·</span>
          <span>
            cache {retrieval.embedding_cache_hits}/{retrieval.embedding_cache_hits + retrieval.embedding_cache_misses || 1} hits
          </span>
          <span>·</span>
          <span>{retrieval.retrieval_enabled ? 'enabled' : 'disabled'}</span>
        </div>
      )}
    </div>
  );
};
