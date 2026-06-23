import React from 'react';
import Card from '../Card';
import { DISK_LEVEL_STYLES } from '../../hooks/useStorageDashboard';

export default function SummaryCard({
  icon,
  title,
  value,
  sub,
  level,
}: {
  icon: React.ReactNode;
  title: string;
  value: string;
  sub?: string;
  level?: 'green' | 'yellow' | 'red';
}) {
  const styles = level ? DISK_LEVEL_STYLES[level] : { icon: 'bg-blue-500/20 text-blue-400', text: 'text-white' };
  return (
    <Card className="flex items-start gap-3">
      <div className={`p-2.5 rounded-lg ${styles.icon}`}>{icon}</div>
      <div className="min-w-0">
        <p className="text-xs text-gray-400 uppercase tracking-wide">{title}</p>
        <p className={`text-xl font-bold truncate ${styles.text}`}>{value}</p>
        {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
      </div>
    </Card>
  );
}
