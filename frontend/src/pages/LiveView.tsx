import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import CameraCard from '../components/CameraCard';
import FullscreenCameraModal from '../components/FullscreenCameraModal';
import CameraSelector from '../components/CameraSelector';
import LiveViewLocationSelector, {
  type BuildingGroup,
} from '../components/LiveViewLocationSelector';
import PageHeader from '../components/PageHeader';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { flushAllUiConsumers } from '../lib/go2rtcConsumerRegistry';
import { destroyAllGo2RtcPlayers } from '../lib/go2rtcPlayer';
import { ensureGo2RtcStreamsSynced, isGo2RtcRunning } from '../lib/liveProvider';
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

const LIVE_LAYOUTS = [
  { cols: 1, label: '1x1' },
  { cols: 2, label: '2x2' },
  { cols: 3, label: '3x3' },
  { cols: 4, label: '4x4' },
  { cols: 5, label: '5x5' },
] as const;

type LiveLayout = (typeof LIVE_LAYOUTS)[number];

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
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

  const [buildings, setBuildings] = useState<BuildingGroup[]>([]);
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

  const openFullscreen = (camera: Camera) => {
    setFullscreenCamera(camera);
    setShowFullscreenModal(true);
  };

  const closeFullscreen = () => {
    setShowFullscreenModal(false);
    setFullscreenCamera(null);
  };

  useEffect(() => {
    let cancelled = false;
    setGo2rtcReady(false);
    void isGo2RtcRunning().then((running) => {
      if (!cancelled && running) setGo2rtcReady(true);
    });
    void ensureGo2RtcStreamsSynced().finally(() => {
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
        const res = await apiFetch('/api/cameras/groups');
        if (!res.ok) throw new Error('Failed to load locations');
        const data = await res.json();
        const list: BuildingGroup[] = data.buildings ?? [];
        const access: PublicCameraAccess = data.cameraAccess ?? { all: true };
        setBuildings(list);

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
  }, []);

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
      setCameras(await res.json());
    } catch (err) {
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

  const selectedFloor =
    selectedGroup &&
    !parseSiteScopeKey(selectedGroup) &&
    !selectedGroup.startsWith('__building__:')
      ? buildingDef?.floorGroups.find((fg) => fg.camera_group === selectedGroup)
      : undefined;

  const onlineIds = cameras.filter((c) => c.online).map((c) => c.id);
  const eagerLive = onlineIds.length > 0 && onlineIds.length <= 4;

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

  if (groupsLoading || !go2rtcReady) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3">
        <Loader2 className="animate-spin text-gray-500" size={48} />
        {!go2rtcReady && (
          <p className="text-sm text-gray-500">Syncing go2rtc streams…</p>
        )}
      </div>
    );
  }

  const sortedCameras = [...cameras].sort((a, b) =>
    cameraTileLabel(a).localeCompare(cameraTileLabel(b)),
  );

  return (
    <>
      <div className="flex flex-col h-full min-h-0">
        <div className="shrink-0 px-4 pt-4 pb-3 border-b border-gray-300 dark:border-gray-700 bg-gray-200 dark:bg-gray-900 z-20">
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
                <select
                  value={selectedLayout.label}
                  onChange={(e) =>
                    setSelectedLayout(
                      LIVE_LAYOUTS.find((l) => l.label === e.target.value) ?? LIVE_LAYOUTS[1],
                    )
                  }
                  className="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-3 py-1"
                >
                  {LIVE_LAYOUTS.map((layout) => (
                    <option key={layout.label} value={layout.label}>
                      {layout.label}
                    </option>
                  ))}
                </select>
              </div>
            }
          />
          <div className="mt-3">
            <LiveViewLocationSelector
              buildings={buildings}
              selectedSite={selectedSite}
              selectedBuildingKey={selectedBuildingKey}
              selectedGroup={selectedGroup}
              onSelectSite={setSelectedSite}
              onSelectBuilding={setSelectedBuildingKey}
              onSelectGroup={setSelectedGroup}
            />
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-4">
          <div className="rounded-lg border border-emerald-700/40 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-100">
            Pick <strong>Site → Building → Floor</strong> to load cameras. Grid uses substream
            102; fullscreen uses main 101.
          </div>

          {(awaitingSite || awaitingBuilding || awaitingFloor) && (
            <div className="flex items-center justify-center py-16 text-gray-500 text-center px-4">
              {awaitingSite && 'Select a site / unit to begin.'}
              {awaitingBuilding && `Select a building / area under ${selectedSite}.`}
              {awaitingFloor &&
                parsedBuilding &&
                `Select a floor / zone under ${parsedBuilding.site} / ${parsedBuilding.building}.`}
            </div>
          )}

          {isSiteAllCameras && !camerasLoading && cameras.length > 0 && (
            <div className="rounded-lg border border-amber-700/40 bg-amber-950/25 px-4 py-2 text-xs text-amber-200">
              Showing all {cameras.length} cameras in {selectedSite}. Select a building and floor to
              reduce load.
            </div>
          )}

          {selectedGroup && camerasLoading && (
            <div className="flex items-center justify-center py-16 gap-2 text-gray-500">
              <Loader2 className="animate-spin" size={24} />
              Loading cameras…
            </div>
          )}

          {selectedGroup && !camerasLoading && cameras.length === 0 && !awaitingFloor && (
            <div className="flex items-center justify-center py-16 text-gray-500">
              No cameras in this location.
            </div>
          )}

          {selectedGroup && !camerasLoading && cameras.length > 0 && (
            <div
              className="grid gap-4"
              style={{
                gridTemplateColumns: `repeat(${selectedLayout.cols}, minmax(0, 1fr))`,
              }}
            >
              {sortedCameras.map((camera) => (
                <div
                  key={camera.id}
                  className={`relative w-full aspect-video ${
                    selectedCamera?.id === camera.id
                      ? 'ring-2 ring-blue-400 dark:ring-blue-500'
                      : ''
                  }`}
                >
                  <div className="absolute inset-0">
                    <CameraCard
                      camera={camera}
                      eagerLive={eagerLive}
                      streamsReady={go2rtcReady}
                      isRecording={recordingSchedule[camera.id] || false}
                      onToggleRecording={() => onToggleRecording(camera.id)}
                      onFullscreen={openFullscreen}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
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
