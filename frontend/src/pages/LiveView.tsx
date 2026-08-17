import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import FullscreenCameraModal from '../components/FullscreenCameraModal';
import CameraSelector from '../components/CameraSelector';
import LiveCameraGrid from '../components/LiveCameraGrid';
import LiveViewLocationSelector, {
  type BuildingGroup,
} from '../components/LiveViewLocationSelector';
import PageHeader from '../components/PageHeader';
import { Loader2, Maximize2, Minimize2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { flushAllUiConsumers } from '../lib/go2rtcConsumerRegistry';
import { destroyAllGo2RtcPlayers, ensureGo2RtcPlayer } from '../lib/go2rtcPlayer';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import { apiFetch, cameraQuery } from '../lib/api';
import {
  parseBuildingScopeKey,
  parseSiteScopeKey,
} from '../constants/corporateFloors';
import {
  initialLiveViewSelection,
  parseBuildingKey,
  type PublicCameraAccess,
} from '../lib/cameraAccess';
import { cameraTileLabel } from '../lib/cameraLabel';
import { useUrlHydration, useUrlSync } from '../hooks/useUrlSearchState';
import { resolveLiveViewFromUrl } from '../lib/urlViewState';
import { useLiveControlRoom } from '../context/LiveControlRoomContext';
import type { LiveCameraGridHandle } from '../components/LiveCameraGrid';

const LIVE_LAYOUTS = [
  { cols: 1, label: '1x1' },
  { cols: 2, label: '2x2' },
  { cols: 3, label: '3x3' },
  { cols: 4, label: '4x4' },
  { cols: 5, label: '5x5' },
  { cols: 6, label: '6x6' },
] as const;

type LiveLayout = (typeof LIVE_LAYOUTS)[number];

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
  liveStatus?: string;
  confirmedOffline?: boolean;
  lastError?: string | null;
  camera_group?: string;
  location_path?: string;
  is_active?: boolean;
}

interface LiveViewProps {
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
}

function LiveView({ recordingSchedule, onToggleRecording }: LiveViewProps) {
  const { params, setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const { controlRoom, setControlRoom } = useLiveControlRoom();
  const gridRef = useRef<LiveCameraGridHandle>(null);
  const savedScrollRowRef = useRef(0);

  const [buildings, setBuildings] = useState<BuildingGroup[]>([]);
  const [configuredSiteNames, setConfiguredSiteNames] = useState<string[]>([]);
  const [selectedSite, setSelectedSite] = useState<string | null>(null);
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [go2rtcReady, setGo2rtcReady] = useState(false);
  const [fullscreenCamera, setFullscreenCamera] = useState<Camera | null>(null);
  const [showFullscreenModal, setShowFullscreenModal] = useState(false);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  const [selectedLayout, setSelectedLayout] = useState<LiveLayout>(LIVE_LAYOUTS[1]);
  const camerasFetchDoneAtRef = useRef<number | null>(null);

  const openFullscreen = (camera: Camera) => {
    setFullscreenCamera(camera);
    setShowFullscreenModal(true);
  };

  const closeFullscreen = () => {
    setShowFullscreenModal(false);
    setFullscreenCamera(null);
  };

  const enterControlRoom = useCallback(() => {
    savedScrollRowRef.current = gridRef.current?.getVisibleStartRow() ?? 0;
    setControlRoom(true);
  }, [setControlRoom]);

  const exitControlRoom = useCallback(() => {
    savedScrollRowRef.current = gridRef.current?.getVisibleStartRow() ?? savedScrollRowRef.current;
    setControlRoom(false);
  }, [setControlRoom]);

  useEffect(() => {
    if (!controlRoom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (showFullscreenModal) return;
      e.preventDefault();
      exitControlRoom();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [controlRoom, showFullscreenModal, exitControlRoom]);

  useEffect(() => {
    const row = savedScrollRowRef.current;
    const id = window.setTimeout(() => gridRef.current?.restoreStartRow(row), 80);
    return () => window.clearTimeout(id);
  }, [controlRoom]);

  useEffect(() => {
    let cancelled = false;
    setGo2rtcReady(false);
    // Preload player JS immediately (in parallel with readiness) so T2→T3
    // does not wait on a cold /go2rtc/video-stream.js download.
    void ensureGo2RtcPlayer().catch(() => {
      // Mount path will surface the error if load still fails.
    });
    void waitForGo2RtcReady().finally(() => {
      if (!cancelled) setGo2rtcReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      destroyAllGo2RtcPlayers();
      flushAllUiConsumers();
    };
  }, []);

  useEffect(() => {
    const loadGroups = async () => {
      setGroupsLoading(true);
      try {
        const res = await apiFetch('/api/cameras/groups?includeStats=false');
        if (!res.ok) throw new Error('Failed to load locations');
        const data = await res.json();
        const list: BuildingGroup[] = data.buildings ?? [];
        const access: PublicCameraAccess = data.cameraAccess ?? { all: true };
        setBuildings(list);
        setConfiguredSiteNames(
          (data.sites ?? [])
            .map((s: { site?: string; name?: string }) => (s.site || s.name || '').trim())
            .filter(Boolean),
        );

        const fromUrl = resolveLiveViewFromUrl(initialParams.current!, list);
        if (fromUrl) {
          setSelectedSite(fromUrl.site);
          setSelectedBuildingKey(fromUrl.buildingKey);
          setSelectedGroup(fromUrl.group);
        } else {
          const initial = initialLiveViewSelection(list, access);
          setSelectedSite(initial.site);
          setSelectedBuildingKey(initial.buildingKey);
          setSelectedGroup(initial.group);
        }

        const layoutLabel = initialParams.current!.get('layout');
        const layoutMatch = layoutLabel
          ? LIVE_LAYOUTS.find((l) => l.label === layoutLabel)
          : undefined;
        if (layoutMatch) setSelectedLayout(layoutMatch);

        markHydrated();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to load locations');
      } finally {
        setGroupsLoading(false);
      }
    };
    void loadGroups();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({
      site: selectedSite,
      building: selectedBuildingKey,
      group: selectedGroup,
      layout: selectedLayout.label,
      camera: selectedCamera?.id ?? null,
      fs: showFullscreenModal && fullscreenCamera ? fullscreenCamera.id : null,
    }),
    [
      selectedSite,
      selectedBuildingKey,
      selectedGroup,
      selectedLayout.label,
      selectedCamera?.id,
      showFullscreenModal,
      fullscreenCamera?.id,
    ],
  );

  useUrlSync(hydratedRef, setParams, urlValues);

  useEffect(() => {
    if (!cameras.length) {
      setSelectedCamera(null);
      return;
    }
    const cameraId = params.get('camera');
    if (cameraId) {
      setSelectedCamera(cameras.find((c) => c.id === cameraId) ?? null);
    }
    const fsId = params.get('fs');
    if (fsId) {
      const fsCam = cameras.find((c) => c.id === fsId);
      if (fsCam) {
        setFullscreenCamera(fsCam);
        setShowFullscreenModal(true);
      }
    }
  }, [cameras, params]);

  const loadCameras = useCallback(async (group: string | null) => {
    if (!group) {
      setCameras([]);
      return;
    }
    setCamerasLoading(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const params: Record<string, string> = {};
      const siteScope = parseSiteScopeKey(group);
      if (siteScope) {
        params.site = siteScope;
      } else {
        const buildingScope = parseBuildingScopeKey(group);
        if (buildingScope) {
          params.building = buildingScope.building;
          params.site = buildingScope.site;
        } else {
          params.camera_group = group;
        }
      }
      const res = await apiFetch(
        `/api/cameras${cameraQuery(params)}`,
        { signal: controller.signal },
      );
      if (!res.ok) throw new Error('Failed to fetch cameras');
      const data = await res.json();
      camerasFetchDoneAtRef.current = performance.now();
      setCameras(data);
    } catch (err) {
      camerasFetchDoneAtRef.current = null;
      const message =
        err instanceof Error && err.name === 'AbortError'
          ? 'Backend not responding — restart the server and refresh.'
          : err instanceof Error
            ? err.message
            : 'Failed to load cameras';
      toast.error(message);
      setCameras([]);
    } finally {
      window.clearTimeout(timeout);
      setCamerasLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCameras(selectedGroup);
  }, [selectedGroup, loadCameras]);

  const parsedBuilding = selectedBuildingKey ? parseBuildingKey(selectedBuildingKey) : null;
  const buildingDef =
    parsedBuilding &&
    buildings.find(
      (b) => b.site === parsedBuilding.site && b.building === parsedBuilding.building,
    );

  const isSiteAllCameras = Boolean(
    selectedSite && selectedGroup && parseSiteScopeKey(selectedGroup) === selectedSite,
  );
  const isBuildingAllCameras = Boolean(
    selectedGroup && parseBuildingScopeKey(selectedGroup),
  );

  const selectedFloor =
    selectedGroup &&
    !parseSiteScopeKey(selectedGroup) &&
    !selectedGroup.startsWith('__building__:')
      ? buildingDef?.floorGroups.find((fg) => fg.camera_group === selectedGroup)
      : undefined;

  // N×N resolution: first screen shows cols×cols tiles; extra cams scroll.
  const gridCols = selectedLayout.cols;

  const sortedCameras = useMemo(
    () =>
      [...cameras].sort((a, b) => cameraTileLabel(a).localeCompare(cameraTileLabel(b))),
    [cameras],
  );

  // Temporary Task-2 timing: API JSON received → first grid paint.
  useEffect(() => {
    if (camerasLoading || sortedCameras.length === 0) return;
    const started = camerasFetchDoneAtRef.current;
    if (started == null) return;
    const raf = requestAnimationFrame(() => {
      const ms = performance.now() - started;
      console.info(
        `[live-grid] api_to_paint_ms=${ms.toFixed(1)} cameras=${sortedCameras.length} cols=${gridCols}`,
      );
      camerasFetchDoneAtRef.current = null;
    });
    return () => cancelAnimationFrame(raf);
  }, [camerasLoading, sortedCameras.length, gridCols]);

  const subtitle = (() => {
    if (!selectedGroup) {
      if (selectedBuildingKey && parsedBuilding) {
        return `Select a floor / zone — ${parsedBuilding.site} / ${parsedBuilding.building}`;
      }
      if (selectedSite) {
        return `Select a building / area — ${selectedSite}`;
      }
      return 'Select site, building, and floor to view cameras — go2rtc sub 102';
    }
    if (isSiteAllCameras && selectedSite) {
      return `${cameras.length} cameras — ${selectedSite} (all cameras) — go2rtc sub 102`;
    }
    if (selectedFloor) {
      return `${cameras.length} cameras — ${selectedFloor.location_path} — go2rtc sub 102`;
    }
    const scope = parseBuildingScopeKey(selectedGroup);
    if (scope) {
      return `${cameras.length} cameras — ${scope.site} / ${scope.building} (all floors) — go2rtc sub 102`;
    }
    return 'Select a floor to view cameras';
  })();

  const awaitingFloor = Boolean(selectedBuildingKey && !selectedGroup);
  const awaitingBuilding = Boolean(selectedSite && !selectedBuildingKey && !isSiteAllCameras);
  const awaitingSite = !selectedSite && !isSiteAllCameras;

  if (groupsLoading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Loader2 className="animate-spin text-gray-500" size={48} />
      </div>
    );
  }

  const layoutSelect = (
    <select
      value={selectedLayout.label}
      onChange={(e) =>
        setSelectedLayout(
          LIVE_LAYOUTS.find((l) => l.label === e.target.value) ?? LIVE_LAYOUTS[1],
        )
      }
      className={
        controlRoom
          ? 'bg-black/70 text-white border border-white/20 rounded px-2 py-1 text-xs'
          : 'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-3 py-1'
      }
      title="Grid layout"
    >
      {LIVE_LAYOUTS.map((layout) => (
        <option key={layout.label} value={layout.label}>
          {layout.label}
        </option>
      ))}
    </select>
  );

  return (
    <>
      <div
        className="relative flex flex-col h-full min-h-0"
        data-live-control-room={controlRoom ? 'true' : 'false'}
      >
        {!controlRoom && (
        <div className="shrink-0 px-3 pt-2 pb-2 border-b border-gray-300 dark:border-gray-700 bg-gray-200 dark:bg-gray-900 z-20">
          <PageHeader
            title="Live View"
            subtitle={subtitle}
            rightContent={
              <div className="flex items-center space-x-4">
                {cameras.length > 0 && (
                  <CameraSelector
                    cameras={cameras}
                    selected={selectedCamera}
                    onSelect={setSelectedCamera}
                  />
                )}
                {layoutSelect}
                <button
                  type="button"
                  onClick={enterControlRoom}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
                  title="Hide navigation and maximize the video wall"
                >
                  <Maximize2 size={14} />
                  Control Room
                </button>
              </div>
            }
          />
          <div className="mt-3">
            <LiveViewLocationSelector
              buildings={buildings}
              extraSiteNames={configuredSiteNames}
              selectedSite={selectedSite}
              selectedBuildingKey={selectedBuildingKey}
              selectedGroup={selectedGroup}
              onSelectSite={setSelectedSite}
              onSelectBuilding={setSelectedBuildingKey}
              onSelectGroup={setSelectedGroup}
            />
          </div>
        </div>
        )}

        <div
          className={`flex-1 min-h-0 overflow-hidden flex flex-col ${
            controlRoom ? 'gap-0 p-0 bg-black' : 'gap-2 p-2'
          }`}
        >
          {(awaitingSite || awaitingBuilding || awaitingFloor) && (
            <div className="flex items-center justify-center flex-1 text-gray-500 text-center px-4">
              {awaitingSite && 'Select a site / unit to begin.'}
              {awaitingBuilding && `Select a building / area under ${selectedSite}.`}
              {awaitingFloor &&
                parsedBuilding &&
                `Select a floor / zone under ${parsedBuilding.site} / ${parsedBuilding.building}.`}
            </div>
          )}

          {!controlRoom && isSiteAllCameras && !camerasLoading && cameras.length > 0 && (
            <div className="shrink-0 rounded-lg border border-amber-700/40 bg-amber-950/25 px-4 py-2 text-xs text-amber-200">
              Showing all {cameras.length} cameras in {selectedSite}. Select a building and floor to
              reduce load.
            </div>
          )}

          {!controlRoom && isBuildingAllCameras && !camerasLoading && cameras.length > 0 && parsedBuilding && (
            <div className="shrink-0 rounded-lg border border-amber-700/40 bg-amber-950/25 px-4 py-2 text-xs text-amber-200">
              Showing all {cameras.length} cameras in {parsedBuilding.building}. Pick a single floor
              to reduce load.
            </div>
          )}

          {selectedGroup && camerasLoading && (
            <div className="flex items-center justify-center flex-1 gap-2 text-gray-500">
              <Loader2 className="animate-spin" size={24} />
              Loading cameras…
            </div>
          )}

          {selectedGroup && !camerasLoading && cameras.length === 0 && !awaitingFloor && (
            <div className="flex items-center justify-center flex-1 text-gray-500">
              No cameras in this location.
            </div>
          )}

          {selectedGroup && !camerasLoading && sortedCameras.length > 0 && (
            <LiveCameraGrid
              ref={gridRef}
              cameras={sortedCameras}
              gridCols={gridCols}
              streamsReady={go2rtcReady}
              selectedCameraId={selectedCamera?.id ?? null}
              fullscreenCameraId={fullscreenCamera?.id ?? null}
              showFullscreenModal={showFullscreenModal}
              recordingSchedule={recordingSchedule}
              onToggleRecording={onToggleRecording}
              onFullscreen={openFullscreen}
              scrollResetKey={selectedGroup}
            />
          )}
        </div>

        {controlRoom && (
          <div className="pointer-events-none absolute inset-x-0 top-0 z-40 flex justify-between p-2">
            <div className="pointer-events-auto opacity-40 hover:opacity-100 focus-within:opacity-100 transition-opacity">
              {layoutSelect}
            </div>
            <button
              type="button"
              onClick={exitControlRoom}
              className="pointer-events-auto inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-black/60 text-white text-xs border border-white/20 hover:bg-black/80"
              title="Exit Control Room (Esc)"
            >
              <Minimize2 size={12} />
              Exit
            </button>
          </div>
        )}
      </div>
      {showFullscreenModal && fullscreenCamera && (
        <FullscreenCameraModal
          key={fullscreenCamera.id}
          camera={fullscreenCamera}
          allCameras={sortedCameras}
          onClose={closeFullscreen}
          onChangeCamera={setFullscreenCamera}
          isRecording={recordingSchedule[fullscreenCamera.id] || false}
          onToggleRecording={onToggleRecording}
        />
      )}
    </>
  );
}

export default React.memo(LiveView);
