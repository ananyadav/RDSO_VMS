import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import FullscreenCameraModal from '../components/FullscreenCameraModal';
import InstantReplayModal from '../components/InstantReplayModal';
import CameraSelector from '../components/CameraSelector';
import LiveCameraGrid from '../components/LiveCameraGrid';
import LiveCameraPool from '../components/LiveCameraPool';
import LiveViewLocationSelector, {
  type BuildingGroup,
} from '../components/LiveViewLocationSelector';
import PageHeader from '../components/PageHeader';
import { Loader2, Maximize2 } from 'lucide-react';
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
  hasUnrestrictedCameraAccess,
  initialLiveViewSelection,
  parseBuildingKey,
  type PublicCameraAccess,
} from '../lib/cameraAccess';
import { cameraTileLabel } from '../lib/cameraLabel';
import { useUrlHydration, useUrlSync } from '../hooks/useUrlSearchState';
import { useIsPhoneLayout } from '../hooks/useMediaQuery';
import { resolveLiveViewFromUrl } from '../lib/urlViewState';
import { useLiveControlRoom } from '../context/LiveControlRoomContext';
import type { LiveCameraGridHandle } from '../components/LiveCameraGrid';
import {
  LIVE_MONITOR_IDS,
  loadLiveMonitorState,
  openLiveMonitorWindow,
  parseMonitorId,
  saveLiveMonitorState,
} from '../lib/liveMonitor';
import {
  assignCameraToSlot,
  assignSequenceToSlot,
  assignedCameraIds,
  assignedSequenceIds,
  buildDefaultAssignments,
  migrateAssignmentsForLayout,
  type SlotAssignments,
} from '../lib/liveTileAssignments';
import { listCameraSequences, type CameraSequence } from '../lib/cameraSequencesApi';
import { useAlarmDisplay } from '../hooks/useAlarmDisplay';
import { LIVE_LAYOUTS, layoutById, type LiveLayoutDef } from '../lib/liveLayouts';
import {
  applyAlarmDisplaySwitch,
  snapshotLiveViewState,
  type LiveViewSnapshot,
} from '../lib/alarmDisplaySwitch';

type LiveLayout = LiveLayoutDef;

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
  ptz?: boolean;
}

interface LiveViewProps {
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
}

function LiveView({ recordingSchedule, onToggleRecording }: LiveViewProps) {
  const { params, setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const { controlRoom, setControlRoom } = useLiveControlRoom();
  const isPhone = useIsPhoneLayout();
  const gridRef = useRef<LiveCameraGridHandle>(null);
  const wallRef = useRef<HTMLDivElement>(null);
  const layoutUserPickedRef = useRef(false);
  const monitorId = useMemo(
    () => parseMonitorId(initialParams.current?.get('monitor') ?? params.get('monitor')),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- identity fixed from first URL
    [],
  );
  const restoredMonitorRef = useRef(false);
  const skipNextAssignmentSeedRef = useRef(false);

  const [buildings, setBuildings] = useState<BuildingGroup[]>([]);
  const [configuredSiteNames, setConfiguredSiteNames] = useState<string[]>([]);
  const [cameraAccess, setCameraAccess] = useState<PublicCameraAccess | null>(null);
  const [selectedSite, setSelectedSite] = useState<string | null>(null);
  const [selectedBuildingKey, setSelectedBuildingKey] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [go2rtcReady, setGo2rtcReady] = useState(true);
  const [fullscreenCamera, setFullscreenCamera] = useState<Camera | null>(null);
  const [showFullscreenModal, setShowFullscreenModal] = useState(false);
  const [instantReplayCamera, setInstantReplayCamera] = useState<Camera | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  const [selectedLayout, setSelectedLayout] = useState<LiveLayout>(LIVE_LAYOUTS[1]);
  const [slotAssignments, setSlotAssignments] = useState<SlotAssignments>([]);
  const [sequences, setSequences] = useState<CameraSequence[]>([]);
  const [sequenceCameras, setSequenceCameras] = useState<Camera[]>([]);
  const prevLayoutColsRef = useRef(selectedLayout.cols);
  const assignmentsGroupRef = useRef<string | null>(null);
  const camerasFetchDoneAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isPhone || layoutUserPickedRef.current) return;
    setSelectedLayout(LIVE_LAYOUTS[0]);
  }, [isPhone]);

  const openFullscreen = useCallback((camera: Camera) => {
    setInstantReplayCamera(null);
    setFullscreenCamera(camera);
    setShowFullscreenModal(true);
  }, []);

  const closeFullscreen = useCallback(() => {
    setShowFullscreenModal(false);
    setFullscreenCamera(null);
  }, []);

  const selectedLayoutRef = useRef(selectedLayout);
  const slotAssignmentsRef = useRef(slotAssignments);
  const authorizedIdsRef = useRef(new Set<string>());
  const alarmSnapshotRef = useRef<LiveViewSnapshot | null>(null);
  selectedLayoutRef.current = selectedLayout;
  slotAssignmentsRef.current = slotAssignments;

  const handleAlarmLayoutSwitch = useCallback(
    (args: {
      event: { id: string; camera_id: string };
      camera: { id: string };
      config: Parameters<typeof applyAlarmDisplaySwitch>[0]['config'];
    }) => {
      const auth = authorizedIdsRef.current;
      if (!auth.has(args.camera.id)) return false;
      if (!alarmSnapshotRef.current) {
        alarmSnapshotRef.current = snapshotLiveViewState(
          args.event.id,
          selectedLayoutRef.current.label,
          slotAssignmentsRef.current,
        );
      }
      const next = applyAlarmDisplaySwitch({
        config: args.config,
        cameraId: args.camera.id,
        prevLayoutLabel: selectedLayoutRef.current.label,
        prevSlots: slotAssignmentsRef.current,
        authorizedCameraIds: auth,
      });
      if (!next) return false;
      layoutUserPickedRef.current = true;
      setSelectedLayout(layoutById(next.layoutLabel));
      setSlotAssignments(next.slots);
      prevLayoutColsRef.current = layoutById(next.layoutLabel).cols;
      return true;
    },
    [],
  );

  const handleAlarmLayoutRestore = useCallback((_eventId: string) => {
    const snap = alarmSnapshotRef.current;
    alarmSnapshotRef.current = null;
    if (!snap) return;
    layoutUserPickedRef.current = true;
    setSelectedLayout(layoutById(snap.layoutLabel));
    setSlotAssignments(snap.slots);
    prevLayoutColsRef.current = layoutById(snap.layoutLabel).cols;
  }, []);

  const {
    activeEvent: alarmDisplayEvent,
    queueCount: alarmQueueCount,
    alarmDisplayActive,
    alarmLayoutSwitchActive,
    manualReset: alarmManualReset,
    acknowledgeActive: alarmAcknowledge,
    onOperatorCloseFullscreen,
  } = useAlarmDisplay({
    cameras,
    enabled: !controlRoom,
    monitorId,
    openFullscreen,
    closeFullscreen,
    onLayoutSwitch: handleAlarmLayoutSwitch,
    onLayoutRestore: handleAlarmLayoutRestore,
  });

  const openInstantReplay = (camera: Camera) => {
    setShowFullscreenModal(false);
    setFullscreenCamera(null);
    setInstantReplayCamera(camera);
  };

  const closeInstantReplay = () => {
    setInstantReplayCamera(null);
  };

  const enterControlRoom = useCallback(() => {
    setShowFullscreenModal(false);
    setFullscreenCamera(null);
    setInstantReplayCamera(null);
    const el = wallRef.current;
    if (!el) return;
    const anyEl = el as HTMLElement & {
      requestFullscreen?: () => Promise<void>;
      webkitRequestFullscreen?: () => void;
    };
    try {
      if (anyEl.requestFullscreen) {
        void anyEl.requestFullscreen().catch(() => {
          toast.error('Could not enter fullscreen');
        });
      } else if (anyEl.webkitRequestFullscreen) {
        anyEl.webkitRequestFullscreen();
      } else {
        toast.error('Browser fullscreen is not available');
      }
    } catch {
      toast.error('Could not enter fullscreen');
    }
  }, []);

  useEffect(() => {
    const syncFullscreen = () => {
      const doc = document as Document & { webkitFullscreenElement?: Element | null };
      const fs = document.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
      const wall = wallRef.current;
      const active = Boolean(wall && fs && (fs === wall || wall.contains(fs)));
      if (active) {
        setControlRoom(true);
        return;
      }
      if (!controlRoom) return;
      setControlRoom(false);
    };
    document.addEventListener('fullscreenchange', syncFullscreen);
    document.addEventListener('webkitfullscreenchange', syncFullscreen as EventListener);
    return () => {
      document.removeEventListener('fullscreenchange', syncFullscreen);
      document.removeEventListener('webkitfullscreenchange', syncFullscreen as EventListener);
    };
  }, [controlRoom, setControlRoom]);

  useEffect(() => {
    let cancelled = false;
    void ensureGo2RtcPlayer().catch(() => {
      // Mount path will surface the error if load still fails.
    });
    void waitForGo2RtcReady().then((ready) => {
      if (!cancelled) setGo2rtcReady(ready !== false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return () => {
      setControlRoom(false);
      destroyAllGo2RtcPlayers();
      flushAllUiConsumers();
    };
  }, [setControlRoom]);

  useEffect(() => {
    const loadGroups = async () => {
      setGroupsLoading(true);
      try {
        const res = await apiFetch('/api/cameras/groups?includeStats=false');
        if (!res.ok) throw new Error('Failed to load locations');
        const data = await res.json();
        const list: BuildingGroup[] = data.buildings ?? [];
        const access: PublicCameraAccess = data.cameraAccess ?? { all: true };
        const unrestricted = hasUnrestrictedCameraAccess(access);
        const visibleBuildings = unrestricted
          ? list
          : list
              .map((b) => ({
                ...b,
                floorGroups: (b.floorGroups || []).filter((fg) => (fg.cameraCount ?? 0) > 0),
              }))
              .filter((b) => b.floorGroups.length > 0);
        setBuildings(visibleBuildings);
        setCameraAccess(access);
        setConfiguredSiteNames(
          unrestricted
            ? (data.sites ?? [])
                .map((s: { site?: string; name?: string }) => (s.site || s.name || '').trim())
                .filter(Boolean)
            : [],
        );

        const fromUrl = resolveLiveViewFromUrl(initialParams.current!, visibleBuildings);
        const saved = loadLiveMonitorState(monitorId);
        if (fromUrl) {
          setSelectedSite(fromUrl.site);
          setSelectedBuildingKey(fromUrl.buildingKey);
          setSelectedGroup(fromUrl.group);
        } else if (saved?.group || saved?.site) {
          setSelectedSite(saved.site);
          setSelectedBuildingKey(saved.building);
          setSelectedGroup(saved.group);
        } else {
          const initial = initialLiveViewSelection(visibleBuildings, access);
          setSelectedSite(initial.site);
          setSelectedBuildingKey(initial.buildingKey);
          setSelectedGroup(initial.group);
        }

        const layoutLabel = initialParams.current!.get('layout') || saved?.layoutLabel;
        if (layoutLabel) setSelectedLayout(layoutById(layoutLabel));

        if (saved?.slots?.length && !fromUrl?.group) {
          skipNextAssignmentSeedRef.current = true;
          setSlotAssignments(saved.slots);
          assignmentsGroupRef.current = saved.group;
          restoredMonitorRef.current = true;
        } else if (saved?.slots?.length && saved.group && fromUrl?.group === saved.group) {
          skipNextAssignmentSeedRef.current = true;
          setSlotAssignments(saved.slots);
          assignmentsGroupRef.current = saved.group;
          restoredMonitorRef.current = true;
        }

        markHydrated();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to load locations');
      } finally {
        setGroupsLoading(false);
      }
    };
    void loadGroups();
  }, [markHydrated, monitorId]);

  const urlValues = useMemo(
    () => ({
      monitor: String(monitorId),
      site: selectedSite,
      building: selectedBuildingKey,
      group: selectedGroup,
      layout: selectedLayout.label,
      camera: selectedCamera?.id ?? null,
      fs: showFullscreenModal && fullscreenCamera ? fullscreenCamera.id : null,
    }),
    [
      monitorId,
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

  useEffect(() => {
    let cancelled = false;
    void listCameraSequences({ enabled: true, limit: 200 })
      .then((data) => {
        if (!cancelled) {
          setSequences(data.items.filter((seq) => seq.enabled && seq.camera_ids.length > 0));
        }
      })
      .catch(() => {
        if (!cancelled) setSequences([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const needed = new Set<string>();
    for (const seq of sequences) {
      for (const id of seq.camera_ids) needed.add(id);
    }
    if (needed.size === 0) {
      setSequenceCameras([]);
      return;
    }
    const localIds = new Set(cameras.map((c) => c.id));
    const missing = [...needed].filter((id) => !localIds.has(id));
    if (missing.length === 0) {
      setSequenceCameras([]);
      return;
    }
    let cancelled = false;
    const q = new URLSearchParams();
    q.set('ids', missing.join(','));
    void apiFetch(`/api/cameras?${q.toString()}`)
      .then(async (res) => {
        if (!res.ok) throw new Error('Failed to load sequence cameras');
        return res.json() as Promise<Camera[]>;
      })
      .then((items) => {
        if (!cancelled) setSequenceCameras(Array.isArray(items) ? items : []);
      })
      .catch(() => {
        if (!cancelled) setSequenceCameras([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sequences, cameras]);

  const cameraById = useMemo(() => {
    const map = new Map<string, Camera>();
    for (const cam of sortedCameras) map.set(cam.id, cam);
    for (const cam of sequenceCameras) {
      if (!map.has(cam.id)) map.set(cam.id, cam);
    }
    return map;
  }, [sortedCameras, sequenceCameras]);

  const sequenceById = useMemo(() => {
    const map = new Map<string, CameraSequence>();
    for (const seq of sequences) map.set(seq.id, seq);
    return map;
  }, [sequences]);

  const authorizedIds = useMemo(
    () => new Set(sortedCameras.map((c) => c.id)),
    [sortedCameras],
  );
  authorizedIdsRef.current = authorizedIds;

  const authorizedSequenceIds = useMemo(
    () => new Set(sequences.map((s) => s.id)),
    [sequences],
  );

  // Reset tile assignments once per location scope load (not on online-status refresh).
  // If cameras arrive after an empty first paint, seed defaults once so tiles (and IR) appear.
  useEffect(() => {
    if (!selectedGroup) {
      setSlotAssignments([]);
      assignmentsGroupRef.current = null;
      return;
    }
    if (camerasLoading) return;
    if (skipNextAssignmentSeedRef.current && assignmentsGroupRef.current === selectedGroup) {
      skipNextAssignmentSeedRef.current = false;
      return;
    }
    const ids = sortedCameras.map((c) => c.id);
    if (assignmentsGroupRef.current === selectedGroup) {
      if (ids.length === 0) return;
      setSlotAssignments((prev) => {
        if (prev.some((a) => a != null)) return prev;
        return buildDefaultAssignments(ids, gridCols);
      });
      return;
    }
    assignmentsGroupRef.current = selectedGroup;
    setSlotAssignments(buildDefaultAssignments(ids, gridCols));
    prevLayoutColsRef.current = gridCols;
  }, [selectedGroup, camerasLoading, sortedCameras, gridCols]);

  // Persist independent layout/camera selection per logical monitor (RDSO 18.1.27).
  useEffect(() => {
    if (!hydratedRef.current) return;
    saveLiveMonitorState(monitorId, {
      layoutLabel: selectedLayout.label,
      site: selectedSite,
      building: selectedBuildingKey,
      group: selectedGroup,
      slots: slotAssignments,
    });
  }, [
    monitorId,
    selectedLayout.label,
    selectedSite,
    selectedBuildingKey,
    selectedGroup,
    slotAssignments,
    hydratedRef,
  ]);

  // Preserve assignments when layout changes during the session.
  useEffect(() => {
    const prevCols = prevLayoutColsRef.current;
    if (prevCols === gridCols) return;
    setSlotAssignments((prev) =>
      migrateAssignmentsForLayout(prev, prevCols, gridCols, authorizedIds, authorizedSequenceIds),
    );
    prevLayoutColsRef.current = gridCols;
  }, [gridCols, authorizedIds, authorizedSequenceIds]);

  const handleAssignCamera = useCallback(
    (slotIndex: number, cameraId: string | null) => {
      if (cameraId && !authorizedIds.has(cameraId)) {
        toast.error('You do not have access to that camera.');
        return;
      }
      setSlotAssignments((prev) => assignCameraToSlot(prev, slotIndex, cameraId));
    },
    [authorizedIds],
  );

  const handleAssignSequence = useCallback(
    (slotIndex: number, sequenceId: string | null) => {
      if (sequenceId && !authorizedSequenceIds.has(sequenceId)) {
        toast.error('You do not have access to that sequence.');
        return;
      }
      setSlotAssignments((prev) => assignSequenceToSlot(prev, slotIndex, sequenceId));
    },
    [authorizedSequenceIds],
  );

  const assignedIds = useMemo(() => assignedCameraIds(slotAssignments), [slotAssignments]);
  const assignedSeqIds = useMemo(() => assignedSequenceIds(slotAssignments), [slotAssignments]);

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

  const layoutOptions = isPhone ? LIVE_LAYOUTS.slice(0, 3) : LIVE_LAYOUTS;

  const layoutSelect = (
    <select
      value={selectedLayout.label}
      onChange={(e) => {
        layoutUserPickedRef.current = true;
        setSelectedLayout(layoutById(e.target.value));
      }}
      className="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-2 py-0.5 sm:px-3 sm:py-1 text-xs sm:text-sm"
      title="Display layout (RDSO 18.2.7 — Full screen / Quad / 4×4 / site divisions)"
      aria-label="Live View grid layout"
    >
      {layoutOptions.map((layout) => (
        <option key={layout.id} value={layout.label}>
          {layout.rdsoName}
        </option>
      ))}
    </select>
  );

  const monitorControls = !isPhone ? (
    <div className="flex items-center gap-1.5" title="Logical display window (RDSO 18.2.24–18.2.26). Place on workstation or external/LFD via OS. Physical 55-inch acceptance is deployment testing.">
      <span className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
        Display {monitorId}/{LIVE_MONITOR_IDS.length}
      </span>
      <select
        className="bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-2 py-0.5 text-xs sm:text-sm"
        defaultValue=""
        onChange={(e) => {
          const next = Number(e.target.value);
          e.target.value = '';
          if (!Number.isFinite(next) || next < 1) return;
          const win = openLiveMonitorWindow(next);
          if (!win) toast.error('Could not open display window (popup blocked?)');
        }}
        aria-label="Open another Live View display window"
      >
        <option value="" disabled>
          Open display…
        </option>
        {LIVE_MONITOR_IDS.filter((id) => id !== monitorId).map((id) => (
          <option key={id} value={id}>
            Display {id}
          </option>
        ))}
      </select>
    </div>
  ) : null;

  return (
    <>
      <div
        className="relative flex flex-col h-full min-h-0"
        data-live-control-room={controlRoom ? 'true' : 'false'}
      >
        <div
          className={`shrink-0 px-2 pt-1.5 pb-1.5 sm:px-3 sm:pt-2 sm:pb-2 border-b border-gray-300 dark:border-gray-700 bg-gray-200 dark:bg-gray-900 z-20 ${
            controlRoom ? 'hidden' : ''
          }`}
        >
          <PageHeader
            title="Live View"
            subtitle={subtitle}
            rightContent={
              <div className="flex items-center gap-1.5 sm:gap-2">
                {monitorControls}
                {cameras.length > 0 && !isPhone && (
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
                  disabled={!selectedGroup || camerasLoading || sortedCameras.length === 0}
                  className="inline-flex items-center justify-center p-1 sm:p-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  title="Fullscreen video wall"
                  aria-label="Fullscreen video wall"
                >
                  <Maximize2 size={16} />
                </button>
              </div>
            }
          />
          {isPhone && selectedGroup && !camerasLoading && cameras.length > 0 && (
            <p className="text-[10px] text-gray-500 dark:text-gray-400 truncate -mt-0.5 mb-1 sm:hidden">
              {cameras.length} camera{cameras.length === 1 ? '' : 's'}
              {selectedFloor ? ` — ${selectedFloor.floor_group || selectedFloor.floor}` : ''}
            </p>
          )}
          <div className="mt-1.5 sm:mt-3">
            <LiveViewLocationSelector
              buildings={buildings}
              extraSiteNames={
                hasUnrestrictedCameraAccess(cameraAccess) ? configuredSiteNames : []
              }
              selectedSite={selectedSite}
              selectedBuildingKey={selectedBuildingKey}
              selectedGroup={selectedGroup}
              onSelectSite={setSelectedSite}
              onSelectBuilding={setSelectedBuildingKey}
              onSelectGroup={setSelectedGroup}
            />
          </div>
        </div>

        <div
          className={`flex-1 min-h-0 overflow-hidden flex flex-col ${
            controlRoom ? 'gap-0 p-0 bg-black' : 'gap-1 p-1 sm:gap-2 sm:p-2'
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
          {alarmLayoutSwitchActive && alarmDisplayEvent && (
            <div
              className="shrink-0 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-600/50 bg-red-950/85 px-3 py-2 text-sm text-white"
              data-testid="alarm-layout-switch-banner"
            >
              <div className="min-w-0">
                <span className="font-semibold uppercase tracking-wide text-red-200 text-xs mr-2">
                  Alarm display
                </span>
                <span className="truncate">
                  {alarmDisplayEvent.title} · layout switch
                  {(alarmQueueCount || 0) > 1 ? ` (+${alarmQueueCount - 1} more)` : ''}
                </span>
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  type="button"
                  className="px-2 py-1 rounded bg-gray-200 text-gray-900 text-xs font-semibold"
                  onClick={() => {
                    void alarmManualReset();
                  }}
                >
                  Reset display
                </button>
                <button
                  type="button"
                  className="px-2 py-1 rounded bg-emerald-700 text-white text-xs font-semibold"
                  onClick={() => {
                    void alarmAcknowledge();
                  }}
                >
                  Acknowledge
                </button>
              </div>
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
            <div
              ref={wallRef}
              className="live-control-room-wall relative flex-1 min-h-0 overflow-hidden flex flex-col md:flex-row bg-black"
              data-live-control-room-wall="true"
            >
              <LiveCameraPool
                cameras={sortedCameras}
                sequences={sequences}
                assignedCameraIds={assignedIds}
                assignedSequenceIds={assignedSeqIds}
                hidden={controlRoom}
                variant="sidebar"
              />
              <LiveCameraGrid
                ref={gridRef}
                slotAssignments={slotAssignments}
                cameraById={cameraById}
                sequenceById={sequenceById}
                gridCols={gridCols}
                streamsReady={go2rtcReady}
                selectedCameraId={controlRoom ? null : selectedCamera?.id ?? null}
                fullscreenCameraId={fullscreenCamera?.id ?? null}
                showFullscreenModal={showFullscreenModal}
                recordingSchedule={recordingSchedule}
                onToggleRecording={onToggleRecording}
                onFullscreen={openFullscreen}
                onInstantReplay={controlRoom ? undefined : openInstantReplay}
                instantReplayCameraId={instantReplayCamera?.id ?? null}
                onSelectCamera={controlRoom ? undefined : setSelectedCamera}
                onAssignCamera={handleAssignCamera}
                onAssignSequence={handleAssignSequence}
                scrollResetKey={selectedGroup}
                controlRoom={controlRoom}
                dragDropEnabled={!controlRoom}
              />
              <LiveCameraPool
                cameras={sortedCameras}
                sequences={sequences}
                assignedCameraIds={assignedIds}
                assignedSequenceIds={assignedSeqIds}
                hidden={controlRoom}
                variant="strip"
              />
            {showFullscreenModal && fullscreenCamera && (
              <FullscreenCameraModal
                key={fullscreenCamera.id}
                camera={fullscreenCamera}
                allCameras={sortedCameras}
                onClose={alarmDisplayActive ? onOperatorCloseFullscreen : closeFullscreen}
                onChangeCamera={setFullscreenCamera}
                isRecording={recordingSchedule[fullscreenCamera.id] || false}
                onToggleRecording={onToggleRecording}
                alarmBanner={
                  alarmDisplayActive && alarmDisplayEvent
                    ? {
                        event: alarmDisplayEvent,
                        queueCount: alarmQueueCount,
                        onManualReset: () => {
                          void alarmManualReset();
                        },
                        onAcknowledge: () => {
                          void alarmAcknowledge();
                        },
                      }
                    : null
                }
              />
            )}
            {instantReplayCamera && (
              <InstantReplayModal
                key={`ir-${instantReplayCamera.id}`}
                camera={instantReplayCamera}
                onClose={closeInstantReplay}
              />
            )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default React.memo(LiveView);
