import React from 'react';
import { Camera, Wifi, WifiOff, PowerOff } from 'lucide-react';
import type { LocationStats } from './LocationTreePanel';

export type CameraListFilter = 'all' | 'online' | 'offline' | 'disabled';

interface FloorSummaryCardsProps {
  stats?: LocationStats;
  floorLabel?: string;
  activeFilter?: CameraListFilter;
  onFilterChange?: (filter: CameraListFilter) => void;
}

const filterCards: {
  filter: CameraListFilter;
  statKey: keyof LocationStats;
  label: string;
  icon: React.ElementType;
  accent: string;
  activeRing: string;
}[] = [
  {
    filter: 'all',
    statKey: 'total',
    label: 'Total Cameras',
    icon: Camera,
    accent: 'text-sky-400 bg-sky-500/10 border-sky-500/20',
    activeRing: 'ring-2 ring-sky-400/60',
  },
  {
    filter: 'online',
    statKey: 'online',
    label: 'Online',
    icon: Wifi,
    accent: 'text-green-400 bg-green-500/10 border-green-500/20',
    activeRing: 'ring-2 ring-green-400/60',
  },
  {
    filter: 'offline',
    statKey: 'offline',
    label: 'Offline',
    icon: WifiOff,
    accent: 'text-red-400 bg-red-500/10 border-red-500/20',
    activeRing: 'ring-2 ring-red-400/60',
  },
  {
    filter: 'disabled',
    statKey: 'disabled',
    label: 'Disabled',
    icon: PowerOff,
    accent: 'text-gray-400 bg-gray-500/10 border-gray-500/20',
    activeRing: 'ring-2 ring-gray-400/60',
  },
];

export default function FloorSummaryCards({
  stats,
  floorLabel,
  activeFilter = 'all',
  onFilterChange,
}: FloorSummaryCardsProps) {
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
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {filterCards.map(({ filter, statKey, label, icon: Icon, accent, activeRing }) => {
        const isActive = activeFilter === filter;
        const title =
          filter === 'offline'
            ? 'Confirmed RTSP/stream probe failure (fresh health check)'
            : filter === 'online'
              ? 'RTSP probe OK or not yet confirmed offline'
              : filter === 'disabled'
                ? 'Cameras manually disabled in Camera Management'
                : floorLabel;

        const body = (
          <>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">
                {label}
              </span>
              <Icon size={14} className="shrink-0 opacity-70" />
            </div>
            <div className="text-lg font-bold tabular-nums text-gray-900 dark:text-white mt-0.5">
              {s[statKey] ?? 0}
            </div>
          </>
        );

        if (onFilterChange) {
          return (
            <button
              key={filter}
              type="button"
              onClick={() => onFilterChange(filter)}
              className={`rounded-lg border px-2.5 py-1.5 text-left transition-shadow hover:brightness-110 ${accent} ${
                isActive ? activeRing : ''
              }`}
              title={title}
            >
              {body}
            </button>
          );
        }

        return (
          <div key={filter} className={`rounded-lg border px-2.5 py-1.5 ${accent}`} title={title}>
            {body}
          </div>
        );
      })}
    </div>
  );
}
