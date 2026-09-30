import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Play } from 'lucide-react';
import toast from 'react-hot-toast';
import CameraCard from './CameraCard';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  type CccCamera,
  cameraHasNoCredentials,
  cccPlaybackHref,
  getCccVmsSource,
  listCccSourceOptions,
  liveWsPathFromMedia,
  type CccSourceDescribe,
} from '../lib/cccVmsSource';
import {
  poolTotalHeight,
  poolVisibleIndexRange,
} from '../lib/liveCameraPoolVirtual';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import { authService } from '../services/authService';

export const CCC_MAX_LIVE_TILES = 16;
const LIST_PAGE = 80;
const ROW_H = 44;

interface Props {
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
  /** Optional: jump to CCC Live tab with same selection semantics */
  onOpenLiveTab?: () => void;
}

/**
 * RDSO 18.6.15.3 — CCC Video Management Screen.
 * List/filter/select live tiles via VMS only; Playback + event/incident deep-links.
 */
export default function CccVideoManagement({
  recordingSchedule,
  onToggleRecording,
  onOpenLiveTab,
}: Props): React.ReactElement {
  const user = authService.getCurrentUser();
  const canLive = hasPermission(user, PERMISSIONS.LIVE_VIEW);
  const canPlayback = hasPermission(user, PERMISSIONS.RECORDING_VIEW);
  const canEvents = hasPermission(user, PERMISSIONS.EVENTS);

  const [vmsSourceId, setVmsSourceId] = useState('local');
  const [sourceOptions, setSourceOptions] = useState<CccSourceDescribe[]>([]);
  const source = useMemo(() => getCccVmsSource(vmsSourceId), [vmsSourceId]);

  useEffect(() => {
    void listCccSourceOptions().then(setSourceOptions);
  }, []);
  const [cameras, setCameras] = useState<CccCamera[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [onlineFilter, setOnlineFilter] = useState<'all' | 'online' | 'offline'>('all');
  const [loading, setLoading] = useState(false);
  const [selectedLive, setSelectedLive] = useState<string[]>([]);
  const [streamsReady, setStreamsReady] = useState(false);
  const [poolScroll, setPoolScroll] = useState(0);
  const poolViewport = 360;

  const load = useCallback(async () => {
    if (!canLive) return;
    setLoading(true);
    try {
      const page = await source.listCameras({
        limit: LIST_PAGE,
        offset,
        q: query.trim() || undefined,
      });
      setCameras(page.items);
      setTotal(page.total);
      // Soft client filter for online (server may not support online= param)
      // Keep ACL-safe list as returned; filter display only.
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Camera list failed');
    } finally {
      setLoading(false);
    }
  }, [canLive, offset, query, source]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (selectedLive.length === 0) {
      setStreamsReady(false);
      return;
    }
    let cancelled = false;
    void waitForGo2RtcReady().then((ok) => {
      if (!cancelled) setStreamsReady(ok);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedLive.length]);

  const filtered = useMemo(() => {
    if (onlineFilter === 'all') return cameras;
    return cameras.filter((c) => (onlineFilter === 'online' ? c.online : !c.online));
  }, [cameras, onlineFilter]);

  const { startIndex, endIndex } = poolVisibleIndexRange(
    filtered.length,
    poolScroll,
    poolViewport,
    ROW_H,
  );
  const virtualItems = filtered.slice(startIndex, Math.max(startIndex, endIndex) + 1);
  const poolHeight = poolTotalHeight(filtered.length, ROW_H);

  const toggleLiveSelect = (id: string) => {
    setSelectedLive((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= CCC_MAX_LIVE_TILES) {
        toast.error(`Select at most ${CCC_MAX_LIVE_TILES} live cameras`);
        return prev;
      }
      return [...prev, id];
    });
  };

  const selectedCameras = useMemo(
    () =>
      selectedLive
        .map((id) => cameras.find((c) => c.id === id) || filtered.find((c) => c.id === id))
        .filter(Boolean) as CccCamera[],
    [cameras, filtered, selectedLive],
  );

  const verifyMediaPath = async (cameraId: string) => {
    try {
      const media = await source.clientMedia(cameraId);
      const path = liveWsPathFromMedia(media);
      toast.success(`VMS media path: ${path}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Media path check failed');
    }
  };

  if (!canLive) {
    return (
      <div className="text-sm text-gray-500 p-6 text-center" data-testid="ccc-video-management">
        Live View permission required for Video Management.
      </div>
    );
  }

  return (
    <div
      className="flex flex-col lg:flex-row gap-3 min-h-0 h-full"
      data-testid="ccc-video-management"
      data-rdso="18.6.15.3"
    >
      <div className="w-full lg:w-80 flex-shrink-0 flex flex-col gap-2 min-h-0">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold">Video Management</div>
          <select
            value={vmsSourceId}
            onChange={(e) => {
              setVmsSourceId(e.target.value);
              setSelectedLive([]);
              setOffset(0);
            }}
            className="text-[10px] px-1 py-0.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          >
            {(sourceOptions.length ? sourceOptions : [{ source_id: 'local', label: 'Local VMS' }]).map(
              (s) => (
                <option key={s.source_id} value={s.source_id}>
                  {s.label || s.source_id}
                </option>
              ),
            )}
          </select>
        </div>
        <p className="text-[10px] text-gray-500">
          Search/filter cameras · select up to {CCC_MAX_LIVE_TILES} live tiles · streams start only for
          selection · CCC → VMS → /media/wN
        </p>
        <div className="flex gap-1">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setOffset(0);
                void load();
              }
            }}
            placeholder="Search name / location…"
            className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          />
          <button
            type="button"
            onClick={() => {
              setOffset(0);
              void load();
            }}
            className="text-xs px-2 py-1 rounded bg-gray-800 text-white"
          >
            Go
          </button>
        </div>
        <select
          value={onlineFilter}
          onChange={(e) => setOnlineFilter(e.target.value as 'all' | 'online' | 'offline')}
          className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        >
          <option value="all">All states</option>
          <option value="online">Online</option>
          <option value="offline">Offline</option>
        </select>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
            <Loader2 className="animate-spin" size={16} /> Loading…
          </div>
        ) : (
          <div
            className="relative overflow-y-auto border border-gray-200 dark:border-gray-700 rounded"
            style={{ height: poolViewport }}
            onScroll={(e) => setPoolScroll(e.currentTarget.scrollTop)}
            data-testid="ccc-vm-camera-pool"
          >
            <div style={{ height: poolHeight, position: 'relative' }}>
              {virtualItems.map((cam, i) => {
                const idx = startIndex + i;
                const top = idx * (ROW_H + 2);
                const checked = selectedLive.includes(cam.id);
                const safe = cameraHasNoCredentials(cam);
                return (
                  <div
                    key={cam.id}
                    className="absolute left-0 right-0 px-2 text-xs hover:bg-gray-50 dark:hover:bg-gray-800"
                    style={{ top, height: ROW_H }}
                  >
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleLiveSelect(cam.id)}
                      />
                      <span className="truncate flex-1 font-medium">{cameraTileLabel(cam)}</span>
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${cam.online ? 'bg-emerald-500' : 'bg-gray-400'}`}
                        title={cam.online ? 'online' : 'offline'}
                      />
                    </label>
                    <div className="pl-6 text-[10px] text-gray-500 truncate flex gap-2">
                      <span className="truncate">{cam.location_path || 'no location'}</span>
                      {!safe ? <span className="text-red-600">creds leak!</span> : null}
                      {canPlayback ? (
                        <Link className="underline shrink-0" to={cccPlaybackHref(cam.id)}>
                          Playback
                        </Link>
                      ) : null}
                      {canEvents ? (
                        <Link
                          className="underline shrink-0"
                          to={`/ccc?tab=events`}
                          title="Event / incident workspace"
                        >
                          Events
                        </Link>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          <button
            type="button"
            disabled={offset <= 0}
            className="px-2 py-0.5 border rounded disabled:opacity-40"
            onClick={() => setOffset((o) => Math.max(0, o - LIST_PAGE))}
          >
            Prev
          </button>
          <span>
            {offset + 1}–{Math.min(offset + cameras.length, total)} / {total}
          </span>
          <button
            type="button"
            disabled={offset + LIST_PAGE >= total}
            className="px-2 py-0.5 border rounded disabled:opacity-40"
            onClick={() => setOffset((o) => o + LIST_PAGE)}
          >
            Next
          </button>
        </div>

        <div className="flex flex-wrap gap-2 text-xs">
          <button
            type="button"
            className="underline text-emerald-700"
            onClick={() => selectedLive[0] && void verifyMediaPath(selectedLive[0])}
            disabled={!selectedLive[0]}
          >
            Verify VMS media path
          </button>
          {onOpenLiveTab ? (
            <button type="button" className="underline" onClick={onOpenLiveTab}>
              Open Live Screen tab
            </button>
          ) : null}
        </div>
      </div>

      <div className="flex-1 min-h-[240px] flex flex-col gap-2">
        <div className="text-xs text-gray-500">
          Live tiles ({selectedCameras.length}/{CCC_MAX_LIVE_TILES}) — CameraCard via VMS/go2rtc
        </div>
        <div
          className="flex-1 min-h-[200px] grid gap-1 bg-black rounded overflow-hidden"
          style={{
            gridTemplateColumns: `repeat(${Math.min(4, Math.max(1, selectedCameras.length))}, minmax(0, 1fr))`,
          }}
          data-testid="ccc-vm-live-grid"
        >
          {selectedCameras.length === 0 ? (
            <div className="col-span-full flex items-center justify-center text-gray-400 text-sm p-6">
              Select cameras to display live feeds (streams not started for unlisted cameras)
            </div>
          ) : (
            selectedCameras.map((cam) => (
              <div key={cam.id} className="relative min-h-[140px] border border-gray-800 flex flex-col">
                <CameraCard
                  camera={{
                    id: cam.id,
                    name: cam.name,
                    displayName: cam.displayName,
                    cameraUid: cam.cameraUid || cam.camera_uid,
                    ip_address: cam.ip_address,
                    online: cam.online,
                    ptz: cam.ptz,
                    workerId: cam.workerId,
                  }}
                  eagerLive
                  streamsReady={streamsReady}
                  liveActive
                  isRecording={Boolean(recordingSchedule[cam.id])}
                  onToggleRecording={onToggleRecording}
                />
                <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-[10px] text-white px-1 py-0.5 flex gap-2 truncate">
                  <span className="truncate">{cam.location_path || cameraTileLabel(cam)}</span>
                  {canPlayback ? (
                    <Link className="underline shrink-0" to={cccPlaybackHref(cam.id)}>
                      <Play size={10} className="inline" /> Archive
                    </Link>
                  ) : null}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
