import React from 'react';
import { HardDrive, Database, Clock, Film } from 'lucide-react';
import Card from '../Card';
import SummaryCard from './SummaryCard';
import {
  StorageDashboardData,
  formatStorageTime,
  diskFreePercent,
  diskStatusLevel,
  diskStatusLabel,
} from '../../hooks/useStorageDashboard';

export default function StorageOverviewTab({ data }: { data: StorageDashboardData }) {
  const { disk, summary, cameras } = data;
  const freePct = diskFreePercent(disk);
  const diskLevel = diskStatusLevel(disk);
  const daysLevel =
    summary.estimated_days_remaining != null && summary.estimated_days_remaining < 7
      ? 'red'
      : summary.estimated_days_remaining != null && summary.estimated_days_remaining < 14
        ? 'yellow'
        : undefined;

  return (
    <div className="space-y-4 w-full">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <SummaryCard
          icon={<Database size={20} />}
          title="Recordings Used"
          value={`${summary.recordings_storage_gb.toFixed(2)} GB`}
          sub={`${summary.total_segments} segments · ${summary.camera_count} cameras`}
        />
        <SummaryCard
          icon={<HardDrive size={20} />}
          title="Free Disk Space"
          value={`${disk.disk_free_gb} GB`}
          sub={`${freePct}% free · ${diskStatusLabel(disk)}`}
          level={diskLevel}
        />
        <SummaryCard
          icon={<Film size={20} />}
          title="Recording Now"
          value={`${summary.cameras_recording} / ${summary.camera_count}`}
          sub={
            summary.combined_gb_per_day != null
              ? `~${summary.combined_gb_per_day} GB/day combined`
              : 'No active recording growth'
          }
        />
        <SummaryCard
          icon={<Clock size={20} />}
          title="Est. Days Remaining"
          value={
            summary.estimated_days_remaining != null
              ? `${summary.estimated_days_remaining} days`
              : '—'
          }
          sub={
            summary.days_remaining_formula
              ? `${summary.days_remaining_formula} = ${summary.estimated_days_remaining} days`
              : summary.combined_gb_per_day
                ? `${disk.disk_free_gb} GB ÷ ${summary.combined_gb_per_day} GB/day`
                : 'No growth rate data yet'
          }
          level={daysLevel}
        />
      </div>

      <Card className="overflow-hidden p-0">
        <div className="px-4 py-3 border-b border-gray-700">
          <h4 className="text-sm font-semibold text-white">Per-Camera Storage</h4>
        </div>
        <div className="overflow-x-auto max-h-[min(420px,50vh)] overflow-y-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-gray-400 uppercase bg-gray-800/80 sticky top-0">
              <tr>
                <th className="px-4 py-3">Camera</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Storage</th>
                <th className="px-4 py-3 text-right">Segments</th>
                <th className="px-4 py-3">Last Recording</th>
                <th className="px-4 py-3 text-right">GB/day</th>
                <th className="px-4 py-3 text-right">Days Left</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700/60">
              {cameras.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                    No recording data on disk yet
                  </td>
                </tr>
              ) : (
                cameras.map((cam) => (
                  <tr key={cam.camera_id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3 font-medium text-white">{cam.camera_name}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                          cam.is_recording
                            ? 'bg-red-500/20 text-red-400'
                            : 'bg-gray-600/50 text-gray-400'
                        }`}
                      >
                        {cam.is_recording ? 'Recording' : 'Idle'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-200 tabular-nums">
                      {cam.storage_used_gb.toFixed(3)} GB
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400 tabular-nums">
                      {cam.segment_count}
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                      {formatStorageTime(cam.latest_segment_time)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400 tabular-nums">
                      {cam.gb_per_day_estimate != null ? cam.gb_per_day_estimate.toFixed(2) : '—'}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {cam.estimated_days_remaining != null ? (
                        <span
                          className={
                            cam.estimated_days_remaining < 7 ? 'text-red-400' : 'text-gray-300'
                          }
                        >
                          {cam.estimated_days_remaining}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
