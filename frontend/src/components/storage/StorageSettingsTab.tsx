import React from 'react';
import { Trash2, Loader2, Gauge } from 'lucide-react';
import Card from '../Card';
import StorageSettingsEditor from './StorageSettingsEditor';
import { StorageDashboardData, formatStorageTime } from '../../hooks/useStorageDashboard';

interface StorageSettingsTabProps {
  data: StorageDashboardData;
  onRunRetention: () => void;
  runningRetention: boolean;
  onSettingsSaved?: () => void;
}

export default function StorageSettingsTab({
  data,
  onRunRetention,
  runningRetention,
  onSettingsSaved,
}: StorageSettingsTabProps) {
  const intervalSec = data.retention?.pass_interval_seconds ?? 300;
  const intervalLabel =
    intervalSec >= 60 && intervalSec % 60 === 0
      ? `Every ${intervalSec / 60} minute${intervalSec / 60 === 1 ? '' : 's'}`
      : `Every ${intervalSec} seconds`;

  return (
    <div className="space-y-4 w-full">
      <StorageSettingsEditor data={data} onSaved={onSettingsSaved} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="text-sm font-semibold text-white mb-4 pb-3 border-b border-gray-700">
            Retention Cleanup
          </h3>

          <div className="space-y-4">
            <div>
              <label className="text-xs text-gray-400 uppercase tracking-wide">Auto-cleanup Interval</label>
              <p className="text-white font-medium mt-1">{intervalLabel}</p>
              <p className="text-xs text-gray-500 mt-1">
                The server scans for recordings older than your retention period on this schedule
                (default every 5 minutes). This is not the length of each video segment.
              </p>
            </div>

            {data.last_retention_pass && (
              <div className="text-sm text-gray-400 bg-gray-700/40 rounded-lg p-3">
                Last cleanup: {formatStorageTime(data.last_retention_pass.ran_at)} —{' '}
                {data.last_retention_pass.freed_gb} GB freed,{' '}
                {data.last_retention_pass.pruned_segments} segments
              </div>
            )}

            <button
              type="button"
              onClick={onRunRetention}
              disabled={runningRetention}
              className="flex items-center gap-2 px-4 py-2 rounded-md bg-amber-700/80 hover:bg-amber-600 text-white text-sm disabled:opacity-50"
            >
              {runningRetention ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Trash2 size={16} />
              )}
              Run Retention Cleanup Now
            </button>
          </div>
        </Card>

        <Card>
          <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-700">
            <Gauge size={18} className="text-gray-400" />
            <h3 className="text-sm font-semibold text-white">Low Storage Thresholds</h3>
          </div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-3 text-center">
              <p className="text-green-400 font-bold">&gt; 20%</p>
              <p className="text-xs text-gray-400 mt-1">Healthy</p>
            </div>
            <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-lg p-3 text-center">
              <p className="text-yellow-400 font-bold">10 – 20%</p>
              <p className="text-xs text-gray-400 mt-1">Low</p>
            </div>
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-center">
              <p className="text-red-400 font-bold">&lt; 10%</p>
              <p className="text-xs text-gray-400 mt-1">Critical</p>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
