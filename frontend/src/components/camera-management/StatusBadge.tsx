import React from 'react';

export type BadgeVariant =
  | 'active'
  | 'disabled'
  | 'online'
  | 'offline'
  | 'recording'
  | 'error'
  | 'neutral';

const STYLES: Record<BadgeVariant, string> = {
  active: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
  disabled: 'bg-gray-500/15 text-gray-400 ring-1 ring-gray-500/25',
  online: 'bg-green-500/15 text-green-400 ring-1 ring-green-500/30',
  offline: 'bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30',
  recording: 'bg-rose-500/15 text-rose-400 ring-1 ring-rose-500/30',
  error: 'bg-red-500/15 text-red-400 ring-1 ring-red-500/30',
  neutral: 'bg-slate-500/15 text-slate-400 ring-1 ring-slate-500/25',
};

interface StatusBadgeProps {
  variant: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

export default function StatusBadge({ variant, children, className = '' }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide ${STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
