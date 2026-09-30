import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  Cpu,
  FileText,
  Flame,
  HeartPulse,
  LayoutDashboard,
  Loader2,
  MonitorPlay,
  Play,
  Shield,
  Video,
} from 'lucide-react';
import toast from 'react-hot-toast';
import PageHeader from '../components/PageHeader';
import CameraCard from '../components/CameraCard';
import CccDashboardPanel from '../components/CccDashboard';
import CccDevicesPanel from '../components/CccDevices';
import CccHealthReports from '../components/CccHealthReports';
import CccHistoricalReports from '../components/CccHistoricalReports';
import CccHotScreen from '../components/CccHotScreen';
import CccIncidentWorkspace from '../components/CccIncidentWorkspace';
import CccOpsConfig from '../components/CccOpsConfig';
import CccVideoManagement from '../components/CccVideoManagement';
import { useSearchParams } from '../hooks/useUrlSearchState';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  type CccCamera,
  cccPlaybackHref,
  getCccVmsSource,
  listCccSourceOptions,
  liveWsPathFromMedia,
  pageCameras,
  type CccSourceDescribe,
} from '../lib/cccVmsSource';
import {
  POOL_DEFAULT_ITEM_HEIGHT,
  poolTotalHeight,
  poolVisibleIndexRange,
} from '../lib/liveCameraPoolVirtual';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import { authService } from '../services/authService';

type TabId =
  | 'overview'
  | 'hot-screen'
  | 'video-management'
  | 'live'
  | 'playback'
  | 'events'
  | 'incidents'
  | 'devices'
  | 'health'
  | 'reports'
  | 'status';

const TAB_IDS: TabId[] = [
  'overview',
  'hot-screen',
  'video-management',
  'live',
  'playback',
  'events',
  'incidents',
  'devices',
  'health',
  'reports',
  'status',
];

function tabFromSearch(raw: string | null): TabId {
  if (raw && (TAB_IDS as string[]).includes(raw)) return raw as TabId;
  return 'overview';
}

const MAX_LIVE_TILES = 16;

interface CccProps {
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
}

export default function Ccc({ recordingSchedule, onToggleRecording }: CccProps): React.ReactElement {
  const user = authService.getCurrentUser();
  const canLive = hasPermission(user, PERMISSIONS.LIVE_VIEW);
  const canPlayback = hasPermission(user, PERMISSIONS.RECORDING_VIEW);
  const canEvents = hasPermission(user, PERMISSIONS.EVENTS);

  const { params: searchParams, setParams } = useSearchParams();
  const [tab, setTab] = useState<TabId>(() => tabFromSearch(searchParams.get('tab')));
  const [cameras, setCameras] = useState<CccCamera[]>([]);
  const [totalCameras, setTotalCameras] = useState(0);
  const [listOffset, setListOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [loadingCams, setLoadingCams] = useState(false);
  const [selectedLive, setSelectedLive] = useState<string[]>([]);
  const [streamsReady, setStreamsReady] = useState(false);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [poolScroll, setPoolScroll] = useState(0);
  const [playbackPick, setPlaybackPick] = useState<string | null>(null);
  const [vmsSourceId, setVmsSourceId] = useState('local');
  const [sourceOptions, setSourceOptions] = useState<CccSourceDescribe[]>([]);

  const source = useMemo(() => getCccVmsSource(vmsSourceId), [vmsSourceId]);

  useEffect(() => {
    void listCccSourceOptions().then(setSourceOptions);
  }, [tab]);

  useEffect(() => {
    setTab(tabFromSearch(searchParams.get('tab')));
  }, [searchParams]);

  const selectTab = (id: TabId) => {
    setTab(id);
    setParams({ tab: id }, { replace: true });
  };

  const loadCameras = useCallback(async () => {
    setLoadingCams(true);
    try {
      // Fetch a large logical page for CCC selection; streams start only for selected tiles.
      const page = await source.listCameras({ limit: 500, offset: 0, q: query.trim() || undefined });
      setCameras(page.items);
      setTotalCameras(page.total);
      setListOffset(0);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load cameras');
    } finally {
      setLoadingCams(false);
    }
  }, [query, source]);

  useEffect(() => {
    void loadCameras();
  }, [loadCameras]);

  useEffect(() => {
    if (tab !== 'status') return;
    let cancelled = false;
    void (async () => {
      try {
        const st = await source.status();
        if (!cancelled) setStatus(st);
      } catch {
        /* status still usable */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, tab]);

  useEffect(() => {
    if (tab !== 'live' || selectedLive.length === 0) return;
    let cancelled = false;
    void waitForGo2RtcReady().then((ok) => {
      if (!cancelled) setStreamsReady(ok);
    });
    return () => {
      cancelled = true;
    };
  }, [tab, selectedLive.length]);

  const toggleLiveSelect = (id: string) => {
    setSelectedLive((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= MAX_LIVE_TILES) {
        toast.error(`Select at most ${MAX_LIVE_TILES} live cameras`);
        return prev;
      }
      return [...prev, id];
    });
  };

  const selectedCameras = useMemo(
    () => selectedLive.map((id) => cameras.find((c) => c.id === id)).filter(Boolean) as CccCamera[],
    [cameras, selectedLive],
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

  const paged = pageCameras(cameras, listOffset, 80);
  const poolViewport = 320;
  const { startIndex, endIndex } = poolVisibleIndexRange(
    cameras.length,
    poolScroll,
    poolViewport,
    POOL_DEFAULT_ITEM_HEIGHT,
  );
  const virtualItems = cameras.slice(startIndex, Math.max(startIndex, endIndex) + 1);
  const poolHeight = poolTotalHeight(cameras.length, POOL_DEFAULT_ITEM_HEIGHT);

  const tabs: { id: TabId; label: string; icon: React.ReactNode; show: boolean }[] = [
    { id: 'overview', label: 'Dashboard', icon: <LayoutDashboard size={14} />, show: true },
    { id: 'hot-screen', label: 'Hot Screen', icon: <Flame size={14} />, show: canEvents },
    { id: 'video-management', label: 'Video Mgmt', icon: <Video size={14} />, show: canLive },
    { id: 'live', label: 'Live', icon: <MonitorPlay size={14} />, show: canLive },
    { id: 'playback', label: 'Playback', icon: <Play size={14} />, show: canPlayback },
    { id: 'events', label: 'Event Log', icon: <Activity size={14} />, show: canEvents },
    { id: 'incidents', label: 'Incidents', icon: <Shield size={14} />, show: canEvents },
    { id: 'devices', label: 'Devices', icon: <Cpu size={14} />, show: canEvents },
    { id: 'health', label: 'Health', icon: <HeartPulse size={14} />, show: canEvents },
    { id: 'reports', label: 'Reports', icon: <FileText size={14} />, show: canEvents },
    { id: 'status', label: 'Status', icon: <Shield size={14} />, show: true },
  ];

  return (
    <div className="h-full min-h-0 flex flex-col p-2 sm:p-3 gap-2" data-testid="ccc-page" data-rdso="18.6">
      <PageHeader
        title="Command Center (CCC)"
        subtitle="Centralized video via VMS — browser only; never direct camera RTSP"
        rightContent={
          <div className="flex items-center gap-2 text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">
            <label className="inline-flex items-center gap-1">
              VMS
              <select
                value={vmsSourceId}
                onChange={(e) => {
                  setVmsSourceId(e.target.value);
                  setSelectedLive([]);
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
            </label>
            <span>{totalCameras} cameras</span>
          </div>
        }
      />

      <div className="flex flex-wrap gap-1 flex-shrink-0">
        {tabs
          .filter((t) => t.show)
          .map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => selectTab(t.id)}
              className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded text-xs font-medium ${
                tab === t.id
                  ? 'bg-emerald-700 text-white'
                  : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 border border-gray-300 dark:border-gray-600'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
      </div>

      <div className="flex-1 min-h-0 overflow-auto rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 p-2 sm:p-3">
        {tab === 'overview' && (
          <CccDashboardPanel onOpenHotScreen={() => selectTab('hot-screen')} />
        )}

        {tab === 'hot-screen' && canEvents && (
          <CccHotScreen
            recordingSchedule={recordingSchedule}
            onToggleRecording={onToggleRecording}
          />
        )}

        {tab === 'video-management' && canLive && (
          <CccVideoManagement
            recordingSchedule={recordingSchedule}
            onToggleRecording={onToggleRecording}
            onOpenLiveTab={() => selectTab('live')}
          />
        )}

        {tab === 'live' && canLive && (
          <div className="flex flex-col lg:flex-row gap-3 min-h-0 h-full">
            <div className="w-full lg:w-72 flex-shrink-0 flex flex-col gap-2 min-h-0">
              <div className="flex gap-1">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search cameras…"
                  className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
                />
                <button
                  type="button"
                  onClick={() => void loadCameras()}
                  className="text-xs px-2 py-1 rounded bg-gray-800 text-white"
                >
                  Go
                </button>
              </div>
              <p className="text-[10px] text-gray-500">
                Select up to {MAX_LIVE_TILES} — streams start only for selected tiles (not the full fleet).
              </p>
              {loadingCams ? (
                <div className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
                  <Loader2 className="animate-spin" size={16} /> Loading…
                </div>
              ) : (
                <div
                  className="relative overflow-y-auto border border-gray-200 dark:border-gray-700 rounded"
                  style={{ height: poolViewport }}
                  onScroll={(e) => setPoolScroll(e.currentTarget.scrollTop)}
                  data-testid="ccc-camera-pool"
                >
                  <div style={{ height: poolHeight, position: 'relative' }}>
                    {virtualItems.map((cam, i) => {
                      const idx = startIndex + i;
                      const top = idx * (POOL_DEFAULT_ITEM_HEIGHT + 4);
                      const checked = selectedLive.includes(cam.id);
                      return (
                        <label
                          key={cam.id}
                          className="absolute left-0 right-0 flex items-center gap-2 px-2 text-xs cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
                          style={{ top, height: POOL_DEFAULT_ITEM_HEIGHT }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleLiveSelect(cam.id)}
                          />
                          <span className="truncate flex-1">{cameraTileLabel(cam)}</span>
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${cam.online ? 'bg-emerald-500' : 'bg-gray-400'}`}
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              <button
                type="button"
                className="text-xs text-left text-emerald-700 dark:text-emerald-400 underline"
                onClick={() => selectedLive[0] && void verifyMediaPath(selectedLive[0])}
                disabled={!selectedLive[0]}
              >
                Verify VMS media path (no RTSP)
              </button>
            </div>

            <div className="flex-1 min-h-[240px] grid gap-1 bg-black rounded overflow-hidden"
              style={{
                gridTemplateColumns: `repeat(${Math.min(4, Math.max(1, selectedCameras.length))}, minmax(0, 1fr))`,
              }}
              data-testid="ccc-live-grid"
            >
              {selectedCameras.length === 0 ? (
                <div className="col-span-full flex items-center justify-center text-gray-400 text-sm p-6">
                  Select cameras to display live feeds via VMS/go2rtc
                </div>
              ) : (
                selectedCameras.map((cam) => (
                  <div key={cam.id} className="relative min-h-[140px] border border-gray-800">
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
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {tab === 'playback' && canPlayback && (
          <div className="space-y-3 max-w-xl">
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Open archived playback through the existing VMS Playback path (search/play by camera identity).
            </p>
            <select
              className="w-full text-sm px-2 py-2 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              value={playbackPick || ''}
              onChange={(e) => setPlaybackPick(e.target.value || null)}
            >
              <option value="">Select camera…</option>
              {paged.page.map((cam) => (
                <option key={cam.id} value={cam.id}>
                  {cameraTileLabel(cam)}
                </option>
              ))}
            </select>
            {totalCameras > paged.limit && (
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  disabled={listOffset <= 0}
                  onClick={() => setListOffset((o) => Math.max(0, o - 80))}
                  className="px-2 py-1 border rounded disabled:opacity-40"
                >
                  Prev
                </button>
                <button
                  type="button"
                  disabled={listOffset + 80 >= cameras.length}
                  onClick={() => setListOffset((o) => o + 80)}
                  className="px-2 py-1 border rounded disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            )}
            {playbackPick ? (
              <Link
                to={cccPlaybackHref(playbackPick)}
                className="inline-flex items-center gap-2 px-3 py-2 rounded bg-emerald-700 text-white text-sm font-medium"
              >
                <Play size={14} /> Open VMS Playback
              </Link>
            ) : null}
          </div>
        )}

        {tab === 'events' && canEvents && <CccIncidentWorkspace initialMode="event-log" />}
        {tab === 'incidents' && canEvents && <CccIncidentWorkspace initialMode="incidents" />}
        {tab === 'devices' && canEvents && <CccDevicesPanel />}
        {tab === 'health' && canEvents && <CccHealthReports />}
        {tab === 'reports' && canEvents && <CccHistoricalReports />}

        {tab === 'status' && (
          <div className="space-y-2">
            <p className="text-sm text-gray-600 dark:text-gray-300">
              Device / server / user status from existing VMS surfaces (no duplicate management).
            </p>
            <pre className="text-[10px] sm:text-xs bg-gray-100 dark:bg-gray-950 p-2 rounded overflow-auto max-h-[40vh]">
              {JSON.stringify(status || { loading: true }, null, 2)}
            </pre>
            <div className="flex flex-wrap gap-2 text-xs">
              <Link className="underline" to="/camera-management">
                Cameras
              </Link>
              <Link className="underline" to="/user-management">
                Users
              </Link>
              <Link className="underline" to="/system-status">
                System status
              </Link>
            </div>
            <CccOpsConfig />
          </div>
        )}
      </div>
    </div>
  );
}
