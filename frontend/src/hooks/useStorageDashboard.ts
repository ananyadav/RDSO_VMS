import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { apiFetch } from '../lib/api';

export interface DiskInfo {
  disk_path: string;
  disk_total_gb: number;
  disk_used_gb: number;
  disk_free_gb: number;
  disk_free_percent?: number;
  disk_percent: number;
  status_level?: 'green' | 'yellow' | 'red';
  status_label?: string;
}

export interface DashboardSummary {
  recordings_storage_gb: number;
  camera_count: number;
  cameras_recording: number;
  total_segments: number;
  combined_gb_per_day: number | null;
  estimated_days_remaining: number | null;
  days_remaining_formula: string | null;
}

export interface CameraStorageRow {
  camera_id: string;
  camera_name: string;
  is_recording: boolean;
  segment_count: number;
  session_count: number;
  storage_used_gb: number;
  latest_segment_time: string | null;
  gb_per_day_estimate: number | null;
  estimated_days_remaining: number | null;
  site?: string;
  building?: string;
  floor?: string;
}

export interface LocationSiteRow {
  site: string;
  total: number;
  recording: number;
  buildings: {
    site: string;
    building: string;
    total: number;
    recording: number;
    floors: { floor: string; total: number; recording: number }[];
  }[];
}

export interface RetentionPolicy {
  label: string;
  retention_hours: number;
  retention_days: number;
  pass_interval_seconds: number;
}

export interface LastRetentionPass {
  ran_at: string;
  freed_gb: number;
  pruned_segments: number;
  deleted_sessions: number;
}

export interface RecordingStreamInfo {
  recording_stream: 'main' | 'sub' | string;
  channel: string;
  quality_label: string;
  substream_warning: boolean;
  stream_profile: string;
  transcode: boolean;
  codec_mode: string;
}

export interface StorageSettings {
  retention_days: number;
  retention_seconds: number;
  retention_label: string;
  recordings_dir: string;
  recordings_dir_editable?: boolean;
  retention_editable?: boolean;
}

export interface StorageDashboardData {
  updated_at: string;
  recordings_root: string;
  stream_profile?: string;
  recording?: RecordingStreamInfo;
  retention: RetentionPolicy;
  storage_settings?: StorageSettings;
  last_retention_pass?: LastRetentionPass | null;
  disk: DiskInfo;
  summary: DashboardSummary;
  cameras: CameraStorageRow[];
  recordingByLocation?: LocationSiteRow[];
  summary_only?: boolean;
}

export function formatStorageTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function diskFreePercent(disk: DiskInfo): number {
  if (disk.disk_free_percent != null && disk.disk_free_percent > 0) {
    return disk.disk_free_percent;
  }
  if (!disk.disk_total_gb) return 0;
  return Math.round((disk.disk_free_gb / disk.disk_total_gb) * 1000) / 10;
}

export function diskStatusLevel(disk: DiskInfo): 'green' | 'yellow' | 'red' {
  if (disk.status_level) return disk.status_level;
  const pct = diskFreePercent(disk);
  if (pct > 20) return 'green';
  if (pct > 10) return 'yellow';
  return 'red';
}

export function diskStatusLabel(disk: DiskInfo): string {
  if (disk.status_label) return disk.status_label;
  const level = diskStatusLevel(disk);
  return level === 'green' ? 'Healthy' : level === 'yellow' ? 'Low' : 'Critical';
}

export const DISK_LEVEL_STYLES = {
  green: { icon: 'bg-green-500/20 text-green-400', text: 'text-green-400', badge: 'text-green-400', bar: 'bg-green-500' },
  yellow: { icon: 'bg-yellow-500/20 text-yellow-400', text: 'text-yellow-400', badge: 'text-yellow-400', bar: 'bg-yellow-500' },
  red: { icon: 'bg-red-500/20 text-red-400', text: 'text-red-400', badge: 'text-red-400', bar: 'bg-red-500' },
};

export function useStorageDashboard() {
  const [data, setData] = useState<StorageDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingFull, setLoadingFull] = useState(false);
  const [runningRetention, setRunningRetention] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async (summaryOnly: boolean) => {
    const url = summaryOnly ? '/api/storage/dashboard?summary=1' : '/api/storage/dashboard';
    const res = await apiFetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as StorageDashboardData;
  }, []);

  const refresh = useCallback(async () => {
    setLoadingFull(true);
    try {
      const json = await fetchDashboard(false);
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load storage data');
    } finally {
      setLoading(false);
      setLoadingFull(false);
    }
  }, [fetchDashboard]);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const summary = await fetchDashboard(true);
        if (cancelled) return;
        setData(summary);
        setError(null);
        setLoading(false);

        setLoadingFull(true);
        const full = await fetchDashboard(false);
        if (cancelled) return;
        setData(full);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load storage data');
          setLoading(false);
        }
      } finally {
        if (!cancelled) setLoadingFull(false);
      }
    };

    void load();
    const interval = setInterval(() => void refresh(), 30000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [fetchDashboard, refresh]);

  const runRetention = useCallback(async () => {
    setRunningRetention(true);
    try {
      const res = await apiFetch('/api/storage/retention/run', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      toast.success(
        `Retention: freed ${result.freed_gb ?? 0} GB, ` +
          `${result.pruned_segments ?? 0} segments, ` +
          `${result.deleted_sessions ?? 0} sessions removed`,
      );
      await refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Retention run failed');
    } finally {
      setRunningRetention(false);
    }
  }, [refresh]);

  return { data, loading, loadingFull, error, refresh, runRetention, runningRetention };
}
