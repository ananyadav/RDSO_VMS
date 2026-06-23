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

export default function StorageDrivesTab({ data }: { data: StorageDashboardData }) {
  const { disk } = data;
  const freePct = diskFreePercent(disk);
  const level = diskStatusLevel(disk);
  const colors = DISK_LEVEL_STYLES[level];
  const usedPct = Math.round((disk.disk_used_gb / disk.disk_total_gb) * 1000) / 10;

  return (
    <div className="space-y-4 w-full">
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <HardDrive size={20} className="text-gray-400" />
            <h3 className="text-lg font-bold text-white">Recordings Volume</h3>
          </div>
          <span className={`text-sm font-semibold ${colors.badge}`}>{diskStatusLabel(disk)}</span>
        </div>

        <div className="w-full bg-gray-600 rounded-full h-4 mb-3">
          <div
            className={`h-4 rounded-full transition-all ${colors.bar}`}
            style={{ width: `${usedPct}%` }}
          />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div>
            <p className="text-xs text-gray-400">Total Capacity</p>
            <p className="text-lg font-bold text-white">{disk.disk_total_gb} GB</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">Used</p>
            <p className="text-lg font-bold text-gray-200">{disk.disk_used_gb} GB</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">Free Space</p>
            <p className={`text-lg font-bold ${colors.text}`}>{disk.disk_free_gb} GB</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">Free %</p>
            <p className={`text-lg font-bold ${colors.text}`}>{freePct}%</p>
          </div>
        </div>

        <p className="text-xs text-gray-500 mt-4 font-mono truncate" title={disk.disk_path}>
          {disk.disk_path}
        </p>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {level !== 'green' && (
          <Card className={`border ${level === 'red' ? 'border-red-500/40' : 'border-yellow-500/40'}`}>
            <div className="flex items-start gap-3">
              <AlertTriangle size={20} className={colors.text} />
              <div>
                <p className={`font-semibold ${colors.text}`}>Low Storage Warning</p>
                <p className="text-sm text-gray-400 mt-1">
                  {level === 'red'
                    ? `Critical: only ${freePct}% disk space remaining. Increase storage or reduce retention/recording cameras.`
                    : `Warning: ${freePct}% free space. Plan for additional storage before full rollout.`}
                </p>
              </div>
            </div>
          </Card>
        )}

        <Card className={level === 'green' ? 'lg:col-span-2' : ''}>
          <h4 className="text-sm font-semibold text-white mb-3">Threshold Reference</h4>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
            <div className="flex items-center gap-2 bg-green-500/10 rounded-lg p-3">
              <span className="w-3 h-3 rounded-full bg-green-500 flex-shrink-0" />
              <span className="text-gray-300">Healthy — free space &gt; 20%</span>
            </div>
            <div className="flex items-center gap-2 bg-yellow-500/10 rounded-lg p-3">
              <span className="w-3 h-3 rounded-full bg-yellow-500 flex-shrink-0" />
              <span className="text-gray-300">Low — free space 10–20%</span>
            </div>
            <div className="flex items-center gap-2 bg-red-500/10 rounded-lg p-3">
              <span className="w-3 h-3 rounded-full bg-red-500 flex-shrink-0" />
              <span className="text-gray-300">Critical — free space &lt; 10%</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
