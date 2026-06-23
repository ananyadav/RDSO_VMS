import React, { useState, useEffect, useCallback } from 'react';
import { Activity, Loader2, AlertTriangle } from 'lucide-react';
import Card from './Card';

interface HealthCamera {
  camera_id: string;
  camera_name: string;
  recording_status: string;
  ffmpeg_status: string;
  health: string;
  health_label: string;
  last_segment_time: string | null;
  last_recording_time: string | null;
  segment_count: number;
}

interface HealthData {
  updated_at: string;
  summary: {
    total: number;
    recording: number;
    healthy: number;
    warning: number;
    reconnecting: number;
    offline: number;
    idle: number;
  };
  cameras: HealthCamera[];
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const HEALTH_STYLES: Record<string, string> = {
  healthy: 'bg-green-500/20 text-green-400 border-green-500/30',
  warning: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  reconnecting: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  offline: 'bg-red-500/20 text-red-400 border-red-500/30',
  idle: 'bg-gray-600/40 text-gray-400 border-gray-600/50',
};

export default function RecordingHealthMonitor(): React.ReactElement {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/api/recordings/health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load health');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 15000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  if (loading && !data) {
    return (
      <Card className="flex items-center justify-center py-8 text-gray-400">
        <Loader2 className="animate-spin mr-2" size={18} />
        Loading recording health…
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

  const { summary, cameras } = data;

  return (
    <div className="w-full">
    <Card className="overflow-hidden p-0 w-full">
      <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={18} className="text-blue-400" />
          <h4 className="text-sm font-semibold text-white">Recording Health Monitor</h4>
        </div>
        <div className="flex gap-3 text-xs text-gray-500">
          <span className="text-green-400">{summary.healthy} healthy</span>
          <span className="text-amber-400">{summary.reconnecting} reconnecting</span>
          <span className="text-yellow-400">{summary.warning} warning</span>
          <span className="text-gray-400">{summary.idle} idle</span>
        </div>
      </div>
      <div className="overflow-x-auto max-h-[min(480px,55vh)] overflow-y-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-gray-400 uppercase bg-gray-800/80 sticky top-0 z-10">
            <tr>
              <th className="px-4 py-3">Camera</th>
              <th className="px-4 py-3">Recording</th>
              <th className="px-4 py-3">FFmpeg</th>
              <th className="px-4 py-3">Health</th>
              <th className="px-4 py-3">Last Segment</th>
              <th className="px-4 py-3">Last Recording</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700/60">
            {cameras.map((cam) => (
              <tr key={cam.camera_id} className="hover:bg-gray-700/30">
                <td className="px-4 py-3 font-medium text-white">{cam.camera_name}</td>
                <td className="px-4 py-3 text-gray-300">{cam.recording_status}</td>
                <td className="px-4 py-3 text-gray-400">{cam.ffmpeg_status}</td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-flex px-2 py-0.5 rounded border text-xs font-medium ${
                      HEALTH_STYLES[cam.health] ?? HEALTH_STYLES.idle
                    }`}
                  >
                    {cam.health_label}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                  {formatTime(cam.last_segment_time)}
                </td>
                <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                  {formatTime(cam.last_recording_time)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
    </div>
  );
}
