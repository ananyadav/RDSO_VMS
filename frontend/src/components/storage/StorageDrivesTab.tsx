import React from 'react';
import { HardDrive, AlertTriangle } from 'lucide-react';
import Card from '../Card';
import {
  StorageDashboardData,
  diskFreePercent,
  diskStatusLevel,
  diskStatusLabel,
  DISK_LEVEL_STYLES,
} from '../../hooks/useStorageDashboard';
import { sanitizeRecordingsPath } from '../../lib/storagePath';

export default function StorageDrivesTab({ data }: { data: StorageDashboardData }) {
  const { disk } = data;
  const freePct = diskFreePercent(disk);
  const level = diskStatusLevel(disk);
  const colors = DISK_LEVEL_STYLES[level];
  const usedPct = Math.round((disk.disk_used_gb / disk.disk_total_gb) * 1000) / 10;

  return (
    <Card className="!p-3 w-full">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <HardDrive size={16} className="text-gray-400 flex-shrink-0" />
          <h3 className="text-sm font-semibold text-white">Recordings Volume</h3>
          <span className={`text-[11px] font-semibold ${colors.badge}`}>{diskStatusLabel(disk)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-gray-500">
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
            &gt;20%
          </span>
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
            10–20%
          </span>
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
            &lt;10%
          </span>
        </div>
      </div>

      <div className="w-full bg-gray-600 rounded-full h-1.5 mb-1.5">
        <div
          className={`h-1.5 rounded-full transition-all ${colors.bar}`}
          style={{ width: `${usedPct}%` }}
        />
      </div>

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-xs">
        <span className="text-gray-500">
          Total <span className="font-semibold text-white tabular-nums">{disk.disk_total_gb} GB</span>
        </span>
        <span className="text-gray-500">
          Used <span className="font-semibold text-gray-200 tabular-nums">{disk.disk_used_gb} GB</span>
        </span>
        <span className="text-gray-500">
          Free <span className={`font-semibold tabular-nums ${colors.text}`}>{disk.disk_free_gb} GB</span>
        </span>
        <span className="text-gray-500">
          Free % <span className={`font-semibold tabular-nums ${colors.text}`}>{freePct}%</span>
        </span>
        <span
          className="text-[10px] text-gray-500 font-mono truncate max-w-full sm:max-w-md sm:ml-auto"
          title={sanitizeRecordingsPath(disk.disk_path)}
        >
          {sanitizeRecordingsPath(disk.disk_path)}
        </span>
      </div>

      {level !== 'green' && (
        <div className="flex items-start gap-1.5 mt-2 text-[11px]">
          <AlertTriangle size={12} className={`${colors.text} flex-shrink-0 mt-0.5`} />
          <p className="text-gray-400">
            {level === 'red'
              ? `Critical: only ${freePct}% free — increase storage or reduce retention.`
              : `Low storage: ${freePct}% free — plan for more capacity.`}
          </p>
        </div>
      )}
    </Card>
  );
}
