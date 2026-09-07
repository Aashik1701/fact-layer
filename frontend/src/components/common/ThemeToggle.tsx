import React from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';

interface ThemeToggleProps {
  className?: string;
}

export const ThemeToggle: React.FC<ThemeToggleProps> = ({ className }) => {
  const { setTheme, isDark } = useTheme();

  return (
    <div
      className={cn(
        'inline-flex items-center p-0.5 rounded-lg border transition-colors',
        isDark
          ? 'bg-slate-900 border-slate-800'
          : 'bg-slate-100 border-slate-200',
        className
      )}
      role="group"
      aria-label="Theme toggle"
    >
      <button
        type="button"
        onClick={() => setTheme('light')}
        title="Light Mode"
        aria-label="Light Mode"
        aria-pressed={!isDark}
        className={cn(
          'p-1.5 rounded-md transition-all flex items-center justify-center',
          !isDark
            ? 'bg-white text-amber-500 shadow-sm'
            : 'text-slate-400 hover:text-slate-200'
        )}
      >
        <Sun className="w-3.5 h-3.5" />
      </button>

      <button
        type="button"
        onClick={() => setTheme('dark')}
        title="Dark Mode"
        aria-label="Dark Mode"
        aria-pressed={isDark}
        className={cn(
          'p-1.5 rounded-md transition-all flex items-center justify-center',
          isDark
            ? 'bg-slate-800 text-sky-400 shadow-sm'
            : 'text-slate-400 hover:text-slate-700'
        )}
      >
        <Moon className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};
