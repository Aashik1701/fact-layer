import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Shown in the fallback header. Defaults to a generic message. */
  label?: string;
  /** Smaller, inline fallback for wrapping one panel/modal rather than the
   *  whole page — used where an isolated render failure (e.g. malformed
   *  graph data) shouldn't take the rest of the app down with it. */
  compact?: boolean;
}

interface ErrorBoundaryState {
  error: Error | null;
}

// React only supports error boundaries as class components — there is no
// hook equivalent. Deliberately does not read ThemeContext: the whole point
// of this component is to still render correctly if something upstream is
// broken, so it uses Tailwind's `dark:` variant (driven by the `dark` class
// on <html>, set outside React in ThemeContext) instead of `useTheme()`.
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('ErrorBoundary caught a render error:', error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const { label = 'Something went wrong rendering this.', compact = false } = this.props;

    return (
      <div
        className={cn(
          'rounded-xl border text-center',
          'bg-white border-slate-200 dark:bg-slate-900/70 dark:border-slate-800',
          compact ? 'p-6' : 'p-12 my-8'
        )}
        role="alert"
      >
        <AlertTriangle className="w-8 h-8 mx-auto mb-3 text-amber-500" aria-hidden="true" />
        <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{label}</p>
        <p className="text-xs font-mono mt-2 text-slate-500 dark:text-slate-500 break-words max-w-md mx-auto">
          {error.message || String(error)}
        </p>
        <p className="text-[11px] mt-1 text-slate-400 dark:text-slate-600">
          This is a display failure, not a finding about your data — the underlying facts and
          relations are unaffected.
        </p>
        <div className="flex items-center justify-center gap-2 mt-4">
          <button
            type="button"
            onClick={this.reset}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-mono font-medium flex items-center gap-1.5 border transition-colors',
              'bg-sky-50 hover:bg-sky-100 text-sky-700 border-sky-200',
              'dark:bg-sky-500/10 dark:hover:bg-sky-500/20 dark:text-sky-400 dark:border-sky-500/20'
            )}
          >
            <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
            Try again
          </button>
          {!compact && (
            <button
              type="button"
              onClick={() => window.location.reload()}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-mono font-medium border transition-colors',
                'bg-slate-100 hover:bg-slate-200 text-slate-600 border-slate-200',
                'dark:bg-slate-800 dark:hover:bg-slate-700 dark:text-slate-300 dark:border-slate-700'
              )}
            >
              Reload page
            </button>
          )}
        </div>
      </div>
    );
  }
}
