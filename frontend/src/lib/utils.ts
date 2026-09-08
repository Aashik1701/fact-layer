import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatValue(value: number | null | undefined, unit?: string | null): string {
  if (value === null || value === undefined) return 'N/A';
  
  // Format nicely
  let formatted: string;
  if (Math.abs(value) >= 1_000_000_000) {
    formatted = `${(value / 1_000_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}B`;
  } else if (Math.abs(value) >= 1_000_000) {
    formatted = `${(value / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`;
  } else {
    formatted = value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  if (unit && unit.trim()) {
    if (unit === '%' || unit === 'percent' || unit === 'percentage') {
      return `${formatted}%`;
    }
    return `${formatted} ${unit}`;
  }
  return formatted;
}

export function formatConfidence(conf: number | null | undefined): string {
  if (conf === null || conf === undefined) return '-';
  return `${Math.round(conf * 100)}%`;
}

export function formatIssuer(issuer: string | null | undefined): string {
  if (!issuer) return 'Unknown Issuer';
  const clean = issuer.toUpperCase();
  if (clean.includes('RBI')) return 'Reserve Bank of India';
  if (clean.includes('IMF')) return 'International Monetary Fund';
  if (clean.includes('MOF') || clean.includes('FINANCE')) return 'Ministry of Finance';
  if (clean.includes('MOSPI')) return 'MoSPI (Govt of India)';
  if (clean.includes('WORLD BANK') || clean.includes('WB')) return 'World Bank';
  return issuer;
}

export function truncate(text: string, maxLength: number): string {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength).trim() + '...';
}
