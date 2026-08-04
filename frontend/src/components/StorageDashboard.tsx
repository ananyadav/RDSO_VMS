import React, { useState, useEffect, useCallback } from 'react';
import { HardDrive, Database, Clock, Film, Loader2, AlertTriangle, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { apiFetch, readJsonResponse } from '../lib/api';
import Card from './Card';

interface DiskInfo {
  disk_path: string;
  disk_total_gb: number;
  disk_used_gb: number;
  disk_free_gb: number;
  disk_free_percent: number;
  disk_percent: number;
  status_level: 'green' | 'yellow' | 'red';
  status_label: string;
}

interface DashboardSummary {
  recordings_storage_gb: number;
  camera_count: number;
  cameras_recording: number;
  total_segments: number;
  combined_gb_per_day: number | null;
  estimated_days_remaining: number | null;
  days_remaining_formula: string | null;
}

interface CameraStorageRow {
  camera_id: string;
  camera_name: string;
  is_recording: boolean;
  segment_count: number;
  session_count: number;
  storage_used_gb: number;
  latest_segment_time: string | null;
  gb_per_day_estimate: number | null;
  estimated_days_remaining: number | null;
}

interface RetentionPolicy {
  label: string;
  retention_hours: number;
  retention_days: number;
  pass_interval_seconds: number;
}

interface LastRetentionPass {
  ran_at: string;
  freed_gb: number;
  pruned_segments: number;
  deleted_sessions: number;
}

interface StorageDashboardData {
  updated_at: string;
  recordings_root: string;
  retention: RetentionPolicy;
  last_retention_pass?: LastRetentionPass | null;
  disk: DiskInfo;
  summary: DashboardSummary;
  cameras: CameraStorageRow[];
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const DISK_LEVEL_STYLES = {
  green: { icon: 'bg-green-500/20 text-green-400', text: 'text-green-400' },
  yellow: { icon: 'bg-yellow-500/20 text-yellow-400', text: 'text-yellow-400' },
  red: { icon: 'bg-red-500/20 text-red-400', text: 'text-red-400' },
};

function diskFreePercent(disk: DiskInfo): number {
  if (disk.disk_free_percent != null && disk.disk_free_percent > 0) {
    return disk.disk_free_percent;
  }
  if (!disk.disk_total_gb) return 0;
  return Math.round((disk.disk_free_gb / disk.disk_total_gb) * 1000) / 10;
}

function diskStatusLevel(disk: DiskInfo): 'green' | 'yellow' | 'red' {
  if (disk.status_level) return disk.status_level;
  const pct = diskFreePercent(disk);
  if (pct > 20) return 'green';
  if (pct > 10) return 'yellow';
  return 'red';
}

function diskStatusLabel(disk: DiskInfo): string {
  if (disk.status_label) return disk.status_label;
  const level = diskStatusLevel(disk);
  return level === 'green' ? 'Healthy' : level === 'yellow' ? 'Low' : 'Critical';
}

function SummaryCard({
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

export default function StorageDashboard(): React.ReactElement {
  const [data, setData] = useState<StorageDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [runningRetention, setRunningRetention] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await apiFetch('/api/storage/dashboard');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await readJsonResponse<StorageDashboardData>(res);
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load storage dashboard');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
    const interval = setInterval(fetchDashboard, 30000);
    return () => clearInterval(interval);
  }, [fetchDashboard]);

  const runRetentionNow = async () => {
    setRunningRetention(true);
    try {
      const res = await apiFetch('/api/storage/retention/run', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await readJsonResponse<Record<string, number>>(res);
      toast.success(
        `Retention: freed ${result.freed_gb ?? 0} GB, ` +
          `${result.pruned_segments ?? 0} segments, ` +
          `${result.deleted_sessions ?? 0} sessions removed`
      );
      await fetchDashboard();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Retention run failed');
    } finally {
      setRunningRetention(false);
    }
  };

  if (loading && !data) {
    return (
      <Card className="flex items-center justify-center py-10 text-gray-400">
        <Loader2 className="animate-spin mr-2" size={20} />
        Loading storage dashboard…
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card className="flex items-center gap-2 py-6 text-red-400">
        <AlertTriangle size={18} />
        {error}
      </Card>
    );
  }

  if (!data) return <></>;

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
    <div className="space-y-4 mb-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold text-white">Recording Storage</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            {data.recordings_root} · auto-delete after{' '}
            <span className="text-gray-400">{data.retention?.label ?? '—'}</span>
            {data.last_retention_pass && (
              <>
                {' '}
                · last cleanup {formatTime(data.last_retention_pass.ran_at)} (
                {data.last_retention_pass.freed_gb} GB freed)
              </>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={runRetentionNow}
            disabled={runningRetention}
            className="text-xs px-3 py-1.5 rounded-md bg-amber-700/80 hover:bg-amber-600 text-white disabled:opacity-50 flex items-center gap-1"
          >
            {runningRetention ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Trash2 size={14} />
            )}
            Run retention
          </button>
          <button
            type="button"
            onClick={() => {
              setLoading(true);
              fetchDashboard();
            }}
            className="text-xs px-3 py-1.5 rounded-md bg-gray-700 hover:bg-gray-600 text-gray-300"
          >
            Refresh
          </button>
        </div>
      </div>

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
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-gray-400 uppercase bg-gray-800/80">
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
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
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
                      {formatTime(cam.latest_segment_time)}
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
