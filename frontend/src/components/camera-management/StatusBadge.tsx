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
  active: 'text-emerald-400',
  disabled: 'text-red-400',
  online: 'text-emerald-400',
  offline: 'text-red-400',
  recording: 'text-rose-400',
  error: 'text-red-400',
  neutral: 'text-gray-400',
};

interface StatusBadgeProps {
  variant: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

export default function StatusBadge({ variant, children, className = '' }: StatusBadgeProps) {
  return (
    <span className={`text-sm ${STYLES[variant]} ${className}`}>
      {children}
    </span>
  );
}
