import React from 'react';
import { Camera, Wifi, WifiOff, PowerOff, Disc, AlertTriangle } from 'lucide-react';
import type { LocationStats } from './LocationTreePanel';

interface FloorSummaryCardsProps {
  stats?: LocationStats;
  floorLabel?: string;
}

const cards: {
  key: keyof LocationStats;
  label: string;
  icon: React.ElementType;
  accent: string;
}[] = [
  { key: 'total', label: 'Total Cameras', icon: Camera, accent: 'text-sky-400 bg-sky-500/10 border-sky-500/20' },
  { key: 'online', label: 'Online', icon: Wifi, accent: 'text-green-400 bg-green-500/10 border-green-500/20' },
  { key: 'offline', label: 'Offline', icon: WifiOff, accent: 'text-amber-400 bg-amber-500/10 border-amber-500/20' },
  { key: 'disabled', label: 'Disabled', icon: PowerOff, accent: 'text-gray-400 bg-gray-500/10 border-gray-500/20' },
  { key: 'recording', label: 'Recording', icon: Disc, accent: 'text-rose-400 bg-rose-500/10 border-rose-500/20' },
  { key: 'errors', label: 'Errors', icon: AlertTriangle, accent: 'text-red-400 bg-red-500/10 border-red-500/20' },
];

export default function FloorSummaryCards({ stats, floorLabel }: FloorSummaryCardsProps) {
  const s: LocationStats = stats ?? {
    total: 0,
    active: 0,
    disabled: 0,
    online: 0,
    offline: 0,
    errors: 0,
    recording: 0,
  };

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2">
      {cards.map(({ key, label, icon: Icon, accent }) => (
        <div
          key={key}
          className={`rounded-lg border px-3 py-2 ${accent}`}
          title={floorLabel}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">
              {label}
            </span>
            <Icon size={14} className="shrink-0 opacity-70" />
          </div>
          <div className="text-xl font-bold tabular-nums text-gray-900 dark:text-white mt-0.5">
            {s[key] ?? 0}
          </div>
        </div>
      ))}
    </div>
  );
}
