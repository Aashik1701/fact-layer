import React, { useEffect } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  maxWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl' | '6xl' | 'full';
  className?: string;
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  maxWidth = '3xl',
  className,
}) => {
  const { isDark } = useTheme();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.body.style.overflow = 'hidden';
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.body.style.overflow = 'unset';
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const maxWidthClasses = {
    sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg',
    xl: 'max-w-xl', '2xl': 'max-w-2xl', '3xl': 'max-w-3xl',
    '4xl': 'max-w-4xl', '5xl': 'max-w-5xl', '6xl': 'max-w-6xl',
    full: 'max-w-[95vw]',
  }[maxWidth];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 md:p-8">
      {/* Backdrop */}
      <div
        className={cn(
          'fixed inset-0 backdrop-blur-md transition-opacity animate-in fade-in duration-200',
          isDark ? 'bg-slate-950/80' : 'bg-slate-900/40'
        )}
        onClick={onClose}
      />

      {/* Modal Dialog */}
      <div
        className={cn(
          'relative w-full rounded-xl border shadow-2xl overflow-hidden flex flex-col max-h-[92vh] z-10 animate-in zoom-in-95 duration-200',
          isDark
            ? 'bg-slate-900 border-slate-700/70'
            : 'bg-white border-slate-200',
          maxWidthClasses,
          className
        )}
      >
        {/* Header */}
        {(title || subtitle) && (
          <div
            className={cn(
              'flex items-start justify-between p-5 border-b sticky top-0 z-10',
              isDark
                ? 'border-slate-800 bg-slate-900/90 backdrop-blur-sm'
                : 'border-slate-100 bg-white/95 backdrop-blur-sm'
            )}
          >
            <div>
              {typeof title === 'string' ? (
                <h3
                  className={cn(
                    'text-lg font-semibold',
                    isDark ? 'text-slate-100' : 'text-slate-900'
                  )}
                >
                  {title}
                </h3>
              ) : (
                title
              )}
              {subtitle && (
                <p className={cn('text-sm mt-1', isDark ? 'text-slate-400' : 'text-slate-500')}>
                  {subtitle}
                </p>
              )}
            </div>
            <button
              onClick={onClose}
              className={cn(
                'p-1.5 rounded-lg transition-colors',
                isDark
                  ? 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                  : 'text-slate-400 hover:text-slate-700 hover:bg-slate-100'
              )}
              aria-label="Close modal"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        )}

        {/* Content */}
        <div
          className={cn(
            'flex-1 overflow-y-auto p-6',
            isDark ? 'bg-slate-900' : 'bg-white'
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
};
