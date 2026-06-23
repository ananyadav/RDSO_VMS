import React, { useEffect, useState } from 'react';
import { FolderOpen, Clock, Save, Loader2, Pencil } from 'lucide-react';
import toast from 'react-hot-toast';
import Card from '../Card';
import { apiFetch } from '../../lib/api';
import type { StorageDashboardData } from '../../hooks/useStorageDashboard';

interface StorageSettingsEditorProps {
  data: StorageDashboardData;
  onSaved?: () => void;
  compact?: boolean;
}

export default function StorageSettingsEditor({
  data,
  onSaved,
  compact = false,
}: StorageSettingsEditorProps): React.ReactElement {
  const retentionDays =
    data.storage_settings?.retention_days ??
    data.retention?.retention_days ??
    15;
  const folder = data.storage_settings?.recordings_dir ?? data.recordings_root ?? '';

  const [days, setDays] = useState(String(retentionDays));
  const [path, setPath] = useState(folder);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDays(String(retentionDays));
    setPath(folder);
  }, [retentionDays, folder]);

  const save = async () => {
    const parsedDays = parseFloat(days);
    if (!Number.isFinite(parsedDays) || parsedDays < 1 || parsedDays > 3650) {
      toast.error('Retention must be between 1 and 3650 days');
      return;
    }
    if (!path.trim()) {
      toast.error('Recording folder path is required');
      return;
    }

    setSaving(true);
    try {
      const res = await apiFetch('/api/storage/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          retention_days: parsedDays,
          recordings_dir: path.trim(),
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Failed to save settings');
      toast.success('Storage settings saved');
      onSaved?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-700">
        <Clock size={18} className="text-gray-400" />
        <h3 className="text-sm font-semibold text-white">Storage Settings</h3>
      </div>

      <div className={`grid gap-4 ${compact ? 'grid-cols-1' : 'grid-cols-1 lg:grid-cols-2'}`}>
        <div>
          <label className="text-xs text-gray-400 uppercase tracking-wide flex items-center gap-1.5">
            Retention Period (days)
            <Pencil size={12} className="text-blue-400" aria-hidden />
          </label>
          <div className="relative mt-1">
            <input
              type="number"
              min={1}
              max={3650}
              step={1}
              value={days}
              onChange={(e) => setDays(e.target.value)}
              className="w-full rounded-md border border-gray-600 bg-gray-800 text-white pl-3 pr-9 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            <Pencil
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none"
              aria-hidden
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Recordings older than this are deleted during retention cleanup.
          </p>
        </div>

        <div>
          <label className="text-xs text-gray-400 uppercase tracking-wide flex items-center gap-1">
            <FolderOpen size={14} />
            Recording Folder
            <Pencil size={12} className="text-blue-400" aria-hidden />
          </label>
          <div className="relative mt-1">
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              className="w-full rounded-md border border-gray-600 bg-gray-800 text-white pl-3 pr-9 py-2 text-sm font-mono focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              placeholder="C:\Recordings or /var/nvr/recordings"
            />
            <Pencil
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 pointer-events-none"
              aria-hidden
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">
            Absolute path on the NVR server where camera recordings are stored.
          </p>
        </div>
      </div>

      <button
        type="button"
        onClick={() => void save()}
        disabled={saving}
        className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-md bg-blue-600 hover:bg-blue-500 text-white text-sm disabled:opacity-50"
      >
        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
        Save Settings
      </button>
    </Card>
  );
}
