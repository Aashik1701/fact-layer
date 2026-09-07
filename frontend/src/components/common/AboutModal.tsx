import React from 'react';
import { Modal } from './Modal';
import { ShieldCheck, GitCompare, Layers, Sparkles } from 'lucide-react';
import { RELATION_CONFIG } from '@/lib/constants';
import { RelationType } from '@/types';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface AboutModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const AboutModal: React.FC<AboutModalProps> = ({ isOpen, onClose }) => {
  const { isDark } = useTheme();

  const stageCls = cn(
    'p-3.5 rounded-lg border',
    isDark ? 'bg-slate-800/50 border-slate-700/60' : 'bg-slate-50 border-slate-200'
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2.5">
          <div className="p-1.5 rounded-lg bg-sky-500/10 border border-sky-500/20 text-sky-500">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <h3 className={cn('text-lg font-bold', isDark ? 'text-slate-100' : 'text-slate-900')}>
              About Fact Knowledge Layer
            </h3>
            <p className={cn('text-xs', isDark ? 'text-slate-400' : 'text-slate-500')}>
              Deterministic Cross-Document Fact Verification &amp; Reasoning
            </p>
          </div>
        </div>
      }
      maxWidth="4xl"
    >
      <div className={cn('space-y-6 text-sm', isDark ? 'text-slate-300' : 'text-slate-600')}>
        {/* Core Thesis Banner */}
        <div
          className={cn(
            'p-4 rounded-xl border',
            isDark
              ? 'bg-gradient-to-r from-sky-950/60 via-slate-900 to-indigo-950/60 border-sky-500/30'
              : 'bg-gradient-to-r from-sky-50 via-white to-indigo-50 border-sky-200'
          )}
        >
          <div className="flex items-center gap-2 text-sky-500 font-semibold tracking-wide text-xs uppercase mb-1">
            <Sparkles className="w-4 h-4" />
            Core Foundational Thesis
          </div>
          <div className={cn('text-xl font-bold font-mono tracking-tight', isDark ? 'text-slate-100' : 'text-slate-900')}>
            COMPARABILITY BEFORE COMPARISON
          </div>
          <p className={cn('mt-2 text-xs sm:text-sm leading-relaxed', isDark ? 'text-slate-300' : 'text-slate-600')}>
            Standard RAG systems and LLMs make false comparisons by placing numbers side-by-side without checking if their underlying qualifiers (time period, geographic scope, methodology, or modality) actually match. The Fact Knowledge Layer introduces a deterministic verification gate that enforces comparability before cross-document reasoning.
          </p>
        </div>

        {/* 4-Stage Pipeline */}
        <div>
          <h4 className={cn('text-sm font-semibold uppercase tracking-wider mb-3 flex items-center gap-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
            <GitCompare className="w-4 h-4 text-sky-500" />
            System Architecture
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { stage: 'STAGE 1', title: 'Grounding & Span Verification', desc: 'Extracts facts and anchors verbatim quotes to pixel-exact PDF bounding boxes. Ungrounded quotes are rejected.' },
              { stage: 'STAGE 2', title: 'Normalization', desc: 'Standardizes units, fiscal periods, and assigns modality (POINT, PROJECTION, ESTIMATE, RANGE).' },
              { stage: 'STAGE 3', title: 'Comparability Gate', desc: 'Deterministic rule engine tests alignment across period, scope, methodology, and modality before comparison.' },
              { stage: 'STAGE 4', title: 'Adjudication', desc: 'Classifies relations, generates audit caveats, and halves confidence (to 0.50) on true contradictions.' },
            ].map((s, i) => (
              <div key={i} className={stageCls}>
                <div className="text-xs font-mono text-sky-500 font-semibold mb-1">{s.stage}</div>
                <div className={cn('font-semibold text-sm mb-1', isDark ? 'text-slate-200' : 'text-slate-800')}>{s.title}</div>
                <p className={cn('text-xs leading-relaxed', isDark ? 'text-slate-400' : 'text-slate-500')}>{s.desc}</p>
              </div>
            ))}
          </div>
        </div>

        {/* 5 Relational Verdicts */}
        <div>
          <h4 className={cn('text-sm font-semibold uppercase tracking-wider mb-3 flex items-center gap-2', isDark ? 'text-slate-400' : 'text-slate-500')}>
            <ShieldCheck className="w-4 h-4 text-emerald-500" />
            The 5 Relational Verdicts
          </h4>
          <div className="space-y-2">
            {(Object.keys(RELATION_CONFIG) as RelationType[]).map((type) => {
              const cfg = RELATION_CONFIG[type];
              return (
                <div
                  key={type}
                  className={cn(
                    'flex items-start gap-3 p-3 rounded-lg border',
                    isDark ? 'bg-slate-800/30 border-slate-800' : 'bg-slate-50 border-slate-200'
                  )}
                >
                  <span className={`px-2.5 py-1 rounded text-xs font-mono font-medium border shrink-0 ${cfg.bgBadge} ${cfg.textBadge} ${cfg.borderBadge}`}>
                    {type}
                  </span>
                  <div className={cn('text-xs leading-relaxed', isDark ? 'text-slate-300' : 'text-slate-600')}>
                    {cfg.description}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Tech Stack */}
        <div
          className={cn(
            'pt-4 border-t flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs',
            isDark ? 'border-slate-800 text-slate-500' : 'border-slate-200 text-slate-400'
          )}
        >
          <div>
            Built with{' '}
            {['FastAPI', 'PyMuPDF', 'React 18', 'TypeScript', 'Tailwind CSS'].map((t, i, arr) => (
              <React.Fragment key={t}>
                <span className={isDark ? 'text-slate-300 font-medium' : 'text-slate-700 font-medium'}>{t}</span>
                {i < arr.length - 1 && <span>, </span>}
              </React.Fragment>
            ))}.
          </div>
          <div className="flex items-center gap-1.5 text-emerald-500 font-mono">
            <ShieldCheck className="w-4 h-4" />
            Strict Zero-Hallucination Pipeline
          </div>
        </div>
      </div>
    </Modal>
  );
};
