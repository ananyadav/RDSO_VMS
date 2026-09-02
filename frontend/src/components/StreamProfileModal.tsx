import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { apiFetch } from '../lib/api';

interface ResolutionOption {
  width: number;
  height: number;
}

interface StreamBlock {
  profile: string;
  channel: string;
  label: string;
  supported: boolean;
  message?: string | null;
  current?: {
    fps?: number | null;
    width?: number | null;
    height?: number | null;
    codec?: string | null;
    resolution?: string | null;
  } | null;
  capabilities?: {
    fps?: { supported: boolean; options: number[]; min: number; max: number };
    resolution?: { supported: boolean; options: ResolutionOption[] };
  } | null;
}

interface StreamProfileResponse {
  ok: boolean;
  cameraId: string;
  ip?: string;
  protocol?: string;
  supported?: boolean;
  message?: string;
  main: StreamBlock;
  sub: StreamBlock;
}

interface StreamProfileModalProps {
  cameraId: string;
  cameraName: string;
  onClose: () => void;
}

function formatResolution(w?: number | null, h?: number | null): string {
  if (w && h) return `${w}×${h}`;
  return '—';
}

function ProfileSection({
  block,
  draft,
  onChange,
}: {
  block: StreamBlock;
  draft: { fps: string; width: string; height: string };
  onChange: (next: { fps: string; width: string; height: string }) => void;
}) {
  if (!block.supported) {
    return (
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-800/50">
        <h3 className="font-semibold text-gray-900 dark:text-gray-100">{block.label}</h3>
        <p className="text-sm text-amber-700 dark:text-amber-300 mt-2">
          {block.message || 'Not supported'}
        </p>
      </div>
    );
  }

  const cur = block.current;
  const fpsCap = block.capabilities?.fps;
  const resCap = block.capabilities?.resolution;
  const resOptions = resCap?.options || [];

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
      <h3 className="font-semibold text-gray-900 dark:text-gray-100">{block.label}</h3>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Channel {block.channel}
        {cur?.codec ? ` · ${cur.codec}` : ''}
      </p>
      <div className="text-sm text-gray-700 dark:text-gray-300">
        <span className="font-medium">Current:</span>{' '}
        {cur?.fps != null ? `${cur.fps} fps` : '—'} ·{' '}
        {cur?.resolution || formatResolution(cur?.width, cur?.height)}
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          FPS (1–25)
        </label>
        {fpsCap?.supported ? (
          <select
            className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
            value={draft.fps}
            onChange={(e) => onChange({ ...draft, fps: e.target.value })}
          >
            {(fpsCap.options || []).map((f) => (
              <option key={f} value={String(f)}>
                {f} fps
              </option>
            ))}
          </select>
        ) : (
          <p className="text-sm text-amber-700 dark:text-amber-300">Not supported</p>
        )}
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Resolution
        </label>
        {resCap?.supported && resOptions.length > 0 ? (
          <select
            className="w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 px-3 py-2 text-sm"
            value={`${draft.width}x${draft.height}`}
            onChange={(e) => {
              const [w, h] = e.target.value.split('x');
              onChange({ ...draft, width: w, height: h });
            }}
          >
            {resOptions.map((o) => (
              <option key={`${o.width}x${o.height}`} value={`${o.width}x${o.height}`}>
                {o.width}×{o.height}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-sm text-amber-700 dark:text-amber-300">Not supported</p>
        )}
      </div>
    </div>
  );
}

export default function StreamProfileModal({ cameraId, cameraName, onClose }: StreamProfileModalProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [data, setData] = useState<StreamProfileResponse | null>(null);
  const [mainDraft, setMainDraft] = useState({ fps: '', width: '', height: '' });
  const [subDraft, setSubDraft] = useState({ fps: '', width: '', height: '' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch(`/api/cameras/${cameraId}/stream-profile`);
      const json = (await res.json()) as StreamProfileResponse;
      if (!res.ok || !json.ok) {
        throw new Error((json as { error?: string }).error || 'Failed to load stream profile');
      }
      setData(json);
      const main = json.main;
      const sub = json.sub;
      if (main?.current) {
        setMainDraft({
          fps: main.current.fps != null ? String(Math.round(main.current.fps)) : '',
          width: main.current.width != null ? String(main.current.width) : '',
          height: main.current.height != null ? String(main.current.height) : '',
        });
      }
      if (sub?.current) {
        setSubDraft({
          fps: sub.current.fps != null ? String(Math.round(sub.current.fps)) : '',
          width: sub.current.width != null ? String(sub.current.width) : '',
          height: sub.current.height != null ? String(sub.current.height) : '',
        });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load stream profile');
      onClose();
    } finally {
      setLoading(false);
    }
  }, [cameraId, onClose]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async () => {
    if (!data) return;
    setSaving(true);
    try {
      const body: Record<string, unknown> = {};
      if (data.main.supported) {
        const mainPayload: Record<string, number> = {};
        if (mainDraft.fps) mainPayload.fps = Number(mainDraft.fps);
        if (mainDraft.width && mainDraft.height) {
          mainPayload.width = Number(mainDraft.width);
          mainPayload.height = Number(mainDraft.height);
        }
        if (Object.keys(mainPayload).length) body.main = mainPayload;
      }
      if (data.sub.supported) {
        const subPayload: Record<string, number> = {};
        if (subDraft.fps) subPayload.fps = Number(subDraft.fps);
        if (subDraft.width && subDraft.height) {
          subPayload.width = Number(subDraft.width);
          subPayload.height = Number(subDraft.height);
        }
        if (Object.keys(subPayload).length) body.sub = subPayload;
      }
      const res = await apiFetch(`/api/cameras/${cameraId}/stream-profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        throw new Error(json.error || 'Failed to apply stream profile');
      }
      toast.success('Stream profile updated on camera');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-900 shadow-xl border border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Stream profile</h2>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {cameraName} · per-camera encoder settings (device ISAPI / ONVIF)
            </p>
          </div>
          <button type="button" onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
            <X size={20} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-gray-500">
              <Loader2 className="animate-spin mr-2" size={20} />
              Reading encoder settings from camera…
            </div>
          ) : data ? (
            <>
              {!data.supported && (
                <p className="text-sm text-amber-700 dark:text-amber-300">
                  {data.message || 'Not supported'} ({data.protocol})
                </p>
              )}
              <ProfileSection block={data.main} draft={mainDraft} onChange={setMainDraft} />
              <ProfileSection block={data.sub} draft={subDraft} onChange={setSubDraft} />
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Changes apply only to this camera on the device. Live View still uses sub in grid and main in
                fullscreen. H.265 sources may be transcoded for browser playback.
              </p>
            </>
          ) : null}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-gray-200 dark:border-gray-700">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 text-sm"
          >
            Close
          </button>
          <button
            type="button"
            disabled={loading || saving || !data?.supported}
            onClick={() => void handleSave()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm disabled:opacity-50"
          >
            {saving ? 'Applying…' : 'Apply to camera'}
          </button>
        </div>
      </div>
    </div>
  );
}
