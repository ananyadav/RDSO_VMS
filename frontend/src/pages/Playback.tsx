import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import PlaybackTimeline, { blockStyle, recordingSeekOffset } from '../components/playback/PlaybackTimeline';
import {
  Play, Pause, ChevronsLeft, ChevronsRight, Calendar, Video,
  Search, Clock, Loader2, Maximize, Minimize, Camera,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { apiFetch, cameraQuery } from '../lib/api';
import {
  ALL_CAMERAS_GROUP,
  buildingScopeKey,
  parseBuildingScopeKey,
} from '../constants/corporateFloors';
import {
  hasUnrestrictedCameraAccess,
  initialPlaybackSelection,
  type PublicCameraAccess,
} from '../lib/cameraAccess';
import { usePlaybackHLS } from '../hooks/usePlaybackHLS';
import { usePlaybackDates } from '../hooks/usePlaybackDates';
import LocationSelector, { type BuildingGroup } from '../components/LocationSelector';
import PageHeader from '../components/PageHeader';
import {
  useUrlHydration,
  useUrlSync,
  parseUrlDate,
  formatUrlDate,
  initialStringParam,
} from '../hooks/useUrlSearchState';
import { resolvePlaybackFromUrl } from '../lib/urlViewState';

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  isLegacy?: boolean;
  camera_group?: string;
  location_path?: string;
}

interface PlaybackRecording {
  sessionId: string;
  startTime: string;
  endTime: string;
  duration: number;
  filePath: string;
  playlistUrl: string;
  status: string;
  segmentCount: number;
  playable?: boolean;
  error?: string | null;
  metadataSource?: 'mongodb' | 'filesystem';
}

const RECORDING_FILE_NOT_FOUND = 'Recording file not found';

function isPlayableRecording(rec: PlaybackRecording): boolean {
  if (rec.playable === false || rec.error) return false;
  if (rec.segmentCount <= 0) return false;
  return true;
}

function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function formatTimeLabel(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

function toApiDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function dayPercent(iso: string): number {
  const d = new Date(iso);
  const secs = d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds();
  return (secs / 86400) * 100;
}

function findRecordingAtDayPercent(
  recordings: PlaybackRecording[],
  selectedDate: Date,
  dayPct: number,
): { rec: PlaybackRecording; offsetSeconds: number } | null {
  const dayStart = new Date(selectedDate);
  dayStart.setHours(0, 0, 0, 0);
  const clickMs = dayStart.getTime() + (dayPct / 100) * 86400 * 1000;

  for (const rec of recordings) {
    const startMs = new Date(rec.startTime).getTime();
    const endMs = new Date(rec.endTime).getTime();
    if (clickMs >= startMs && clickMs <= endMs) {
      return { rec, offsetSeconds: Math.max(0, (clickMs - startMs) / 1000) };
    }
  }
  return null;
}

const CustomDay: React.FC<{
  day: number;
  hasRecording: boolean;
  isSelected: boolean;
  onClick: (day: number) => void;
}> = ({ day, hasRecording, isSelected, onClick }) => (
  <button
    type="button"
    onClick={() => onClick(day)}
    className={`relative w-8 h-8 flex items-center justify-center text-xs rounded-full ${
      isSelected ? 'bg-red-600 text-white font-semibold' : ''
    } ${hasRecording && !isSelected ? 'text-blue-300 font-bold' : 'text-gray-400'} hover:bg-gray-700`}
  >
    {hasRecording && !isSelected && (
      <span className="absolute top-0.5 left-0.5 w-0 h-0 border-t-[5px] border-r-[5px] border-t-blue-400 border-r-transparent" />
    )}
    {day}
  </button>
);

export default function Playback(): React.ReactElement {
  const { params, setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const autoSearchRef = useRef(false);

  const [buildings, setBuildings] = useState<BuildingGroup[]>([]);
  const [cameraAccess, setCameraAccess] = useState<PublicCameraAccess | null>(null);
  const [selectedBuilding, setSelectedBuilding] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraFilter, setCameraFilter] = useState(() =>
    initialStringParam(initialParams, 'q'),
  );
  const [selectedCamera, setSelectedCamera] = useState<Camera | null>(null);
  const [selectedDate, setSelectedDate] = useState<Date>(new Date());
  const [isSearching, setIsSearching] = useState(false);
  const [recordings, setRecordings] = useState<PlaybackRecording[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [seekOnLoad, setSeekOnLoad] = useState<number | null>(null);
  const [playbackSpeed, setPlaybackSpeed] = useState(() => {
    const raw = initialParams.current?.get('speed');
    const n = raw ? Number(raw) : 1;
    return Number.isFinite(n) && n > 0 ? n : 1;
  });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [gapNotice, setGapNotice] = useState<string | null>(null);
  const videoContainerRef = useRef<HTMLDivElement>(null);

  const GAP_MESSAGE = 'No recording available for this time.';

  const calYear = selectedDate.getFullYear();
  const calMonth = selectedDate.getMonth() + 1;
  const { dates: recordedDayKeys } = usePlaybackDates(
    selectedCamera?.id ?? null,
    selectedCamera?.cameraUid,
    calYear,
    calMonth,
  );

  const playableRecordings = useMemo(
    () => recordings.filter(isPlayableRecording),
    [recordings],
  );

  const activeRecording = useMemo(
    () => recordings.find((r) => r.sessionId === activeSessionId) ?? null,
    [recordings, activeSessionId],
  );

  const {
    videoRef,
    loading: videoLoading,
    error: videoError,
    isPlaying,
    currentTime,
    duration,
    togglePlayPause,
    seek,
    setPlaybackRate,
  } = usePlaybackHLS(activeRecording?.playlistUrl ?? null, seekOnLoad);

  const effectiveDuration = useMemo(() => {
    if (duration > 0 && Number.isFinite(duration)) return duration;
    return activeRecording?.duration ?? 0;
  }, [duration, activeRecording]);

  const playheadPercent = useMemo(() => {
    if (!activeRecording) return null;
    const block = blockStyle(activeRecording, selectedDate);
    const blockLeft = parseFloat(block.left);
    const blockWidth = parseFloat(block.width);
    if (effectiveDuration > 0) {
      return blockLeft + (currentTime / effectiveDuration) * blockWidth;
    }
    return dayPercent(activeRecording.startTime);
  }, [activeRecording, currentTime, effectiveDuration, selectedDate]);

  const absoluteTimeLabel = useMemo(() => {
    if (!activeRecording) return '00:00:00';
    const start = new Date(activeRecording.startTime);
    start.setSeconds(start.getSeconds() + Math.floor(currentTime));
    return start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }, [activeRecording, currentTime]);

  useEffect(() => {
    setPlaybackRate(playbackSpeed);
  }, [playbackSpeed, setPlaybackRate]);

  useEffect(() => {
    if (seekOnLoad != null && duration > 0) {
      setSeekOnLoad(null);
    }
  }, [seekOnLoad, duration]);

  useEffect(() => {
    const onFs = () => {
      setIsFullscreen(document.fullscreenElement === videoContainerRef.current);
    };
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
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
        setCameraAccess(access);

        const fromUrl = resolvePlaybackFromUrl(initialParams.current!, list);
        if (fromUrl?.building && fromUrl.group) {
          setSelectedBuilding(fromUrl.building);
          setSelectedGroup(fromUrl.group);
        } else {
          const initial = initialPlaybackSelection(list, access);
          if (initial) {
            setSelectedBuilding(initial.building);
            setSelectedGroup(initial.group);
          }
        }

        const dateParam = parseUrlDate(initialParams.current!.get('date'));
        if (dateParam) setSelectedDate(dateParam);

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
      building: selectedBuilding,
      group: selectedGroup,
      camera: selectedCamera?.id ?? null,
      date: selectedCamera ? formatUrlDate(selectedDate) : null,
      session: activeSessionId,
      q: cameraFilter.trim() || null,
      speed: playbackSpeed !== 1 ? String(playbackSpeed) : null,
    }),
    [
      selectedBuilding,
      selectedGroup,
      selectedCamera?.id,
      selectedDate,
      activeSessionId,
      cameraFilter,
      playbackSpeed,
    ],
  );

  useUrlSync(hydratedRef, setParams, urlValues);

  useEffect(() => {
    if (!cameras.length) return;
    const cameraId = params.get('camera');
    if (!cameraId) return;
    const cam = cameras.find((c) => c.id === cameraId);
    if (cam) setSelectedCamera(cam);
  }, [cameras, params]);

  const loadCameras = useCallback(async (group: string | null) => {
    if (!group && hasUnrestrictedCameraAccess(cameraAccess)) {
      setCameras([]);
      return;
    }
    setCamerasLoading(true);
    try {
      const params: Record<string, string> = { forPlayback: '1' };
      const unrestricted = hasUnrestrictedCameraAccess(cameraAccess);
      if (unrestricted && group) {
        if (group === ALL_CAMERAS_GROUP) {
          // all allowed cameras (admin)
        } else {
          const buildingScope = parseBuildingScopeKey(group);
          if (buildingScope) {
            params.building = buildingScope.building;
            params.site = buildingScope.site;
          } else {
            params.camera_group = group;
          }
        }
      }
      // Restricted users: no location filter — API returns only permitted cameras.
      const res = await apiFetch(`/api/cameras${cameraQuery(params)}`);
      if (!res.ok) throw new Error('Failed to load cameras');
      setCameras(await res.json());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to load cameras');
      setCameras([]);
    } finally {
      setCamerasLoading(false);
    }
  }, [cameraAccess]);

  useEffect(() => {
    if (!cameraAccess) return;
    if (hasUnrestrictedCameraAccess(cameraAccess) && !selectedGroup) {
      setCameras([]);
      return;
    }
    void loadCameras(selectedGroup);
  }, [selectedGroup, cameraAccess, loadCameras]);

  const handleSelectBuilding = (building: string) => {
    if (building === ALL_CAMERAS_GROUP) {
      if (!hasUnrestrictedCameraAccess(cameraAccess)) return;
      setSelectedBuilding(ALL_CAMERAS_GROUP);
      setSelectedGroup(ALL_CAMERAS_GROUP);
      return;
    }
    setSelectedBuilding(building);
    const b = buildings.find((x) => x.building === building);
    if (b) {
      setSelectedGroup(buildingScopeKey(b.site, b.building));
    } else {
      setSelectedGroup(null);
    }
  };

  useEffect(() => {
    if (!selectedCamera) return;
    if (!cameras.some((c) => c.id === selectedCamera.id)) {
      setSelectedCamera(null);
      setRecordings([]);
      setActiveSessionId(null);
      setSeekOnLoad(null);
      setGapNotice(null);
    }
  }, [cameras, selectedCamera]);

  const selectedFloor = buildings
    .find((b) => b.building === selectedBuilding)
    ?.floorGroups.find((fg) => fg.camera_group === selectedGroup);

  const filteredCameras = cameras.filter((c) => {
    const label = (c.displayName || c.name).toLowerCase();
    return label.includes(cameraFilter.toLowerCase());
  });

  const handleSearch = async () => {
    if (!selectedCamera) {
      toast.error('Select a camera first');
      return;
    }
    setIsSearching(true);
    setActiveSessionId(null);
    setSeekOnLoad(null);
    setGapNotice(null);
    setRecordings([]);
    try {
      const date = toApiDate(selectedDate);
      const ref = selectedCamera.cameraUid || selectedCamera.id;
      const url = `/api/playback/search?cameraUid=${encodeURIComponent(ref)}&date=${date}`;
      const res = await apiFetch(url);
      if (res.status === 404) {
        const err = await res.json().catch(() => ({}));
        const msg = err.error as string | undefined;
        if (!msg || msg === 'Not Found') {
          throw new Error('Playback search API not available — restart the backend server');
        }
        throw new Error(msg);
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err.error as string) || `Search failed (${res.status})`);
      }
      const data = await res.json();
      const list: PlaybackRecording[] = data.recordings || [];
      const playable = list.filter(isPlayableRecording);
      setRecordings(playable);
      const sessionFromUrl = initialParams.current?.get('session');
      if (sessionFromUrl && playable.some((r) => r.sessionId === sessionFromUrl)) {
        setActiveSessionId(sessionFromUrl);
      }
      if (playable.length > 0) {
        if (!autoSearchRef.current) {
          toast.success(`Found ${playable.length} recording session(s)`);
        }
      } else if (list.length > 0) {
        toast('Sessions exist but files are missing on disk', { icon: '⚠️' });
      } else if (!autoSearchRef.current) {
        toast('No recordings for this date', { icon: '📭' });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setIsSearching(false);
    }
  };

  useEffect(() => {
    if (!hydratedRef.current || autoSearchRef.current || !selectedCamera) return;
    if (!initialParams.current?.get('date')) return;
    autoSearchRef.current = true;
    void handleSearch();
  }, [selectedCamera]);

  const handleGapSelection = (dayPct: number) => {
    const hit = findRecordingAtDayPercent(recordings, selectedDate, dayPct);
    if (hit) return;
    setGapNotice(GAP_MESSAGE);
    toast(GAP_MESSAGE, { icon: 'ℹ️' });
  };

  const playAt = (rec: PlaybackRecording, offsetSeconds = 0) => {
    if (!isPlayableRecording(rec)) {
      const msg = rec.error ?? RECORDING_FILE_NOT_FOUND;
      setGapNotice(msg);
      toast.error(msg);
      return;
    }
    setGapNotice(null);
    const maxOffset = rec.duration > 0 ? rec.duration : offsetSeconds;
    const clampedOffset = Math.max(0, Math.min(maxOffset, offsetSeconds));

    if (rec.sessionId === activeSessionId) {
      seek(clampedOffset);
      return;
    }
    setSeekOnLoad(clampedOffset);
    setActiveSessionId(rec.sessionId);
  };

  const toggleFullscreen = async () => {
    const el = videoContainerRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await el.requestFullscreen();
      }
    } catch {
      toast.error('Fullscreen not available');
    }
  };

  const captureSnapshot = () => {
    const video = videoRef.current;
    if (!video || video.readyState < 2 || !video.videoWidth) {
      toast.error('No frame to capture — start playback first');
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      toast.error('Could not capture image');
      return;
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) {
        toast.error('Could not capture image');
        return;
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const timePart = absoluteTimeLabel.replace(/[:\s]/g, '-');
      const camName = (selectedCamera?.name ?? 'camera').replace(/\s+/g, '_');
      link.href = url;
      link.download = `${camName}_${toApiDate(selectedDate)}_${timePart}.png`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success('Snapshot saved');
    }, 'image/png');
  };

  const renderSessionsList = () => (
    <div className="flex flex-col flex-1 min-h-0 border-t border-gray-700">
      <div className="flex-shrink-0 px-2.5 py-2 text-white text-xs font-semibold flex items-center">
        <Video size={14} className="mr-1.5 text-blue-400" />
        Sessions
        {playableRecordings.length > 0 && (
          <span className="ml-auto text-gray-500 font-normal">{playableRecordings.length}</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-1.5 pb-1.5 space-y-1 min-h-0">
        {playableRecordings.length === 0 ? (
          <p className="text-center text-gray-500 text-[10px] py-4 px-1">
            {recordings.length > 0
              ? 'No playable recordings for this date'
              : 'Search recordings to load sessions'}
          </p>
        ) : (
          playableRecordings.map((rec) => {
            const active = rec.sessionId === activeSessionId;
            return (
              <button
                key={rec.sessionId}
                type="button"
                onClick={() => playAt(rec, 0)}
                className={`w-full text-left p-1.5 rounded border text-[10px] transition-all ${
                  active
                    ? 'bg-blue-600/20 border-blue-500/40'
                    : 'bg-gray-700/40 border-gray-600 hover:bg-gray-700'
                }`}
              >
                <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
                  <span className="text-gray-500">Start</span>
                  <span className="text-gray-200 font-medium">{formatTimeLabel(rec.startTime)}</span>
                  <span className="text-gray-500">End</span>
                  <span className="text-gray-200 font-medium">{formatTimeLabel(rec.endTime)}</span>
                  <span className="text-gray-500">Duration</span>
                  <span className="text-gray-300">{formatClock(rec.duration)}</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );

  const renderCalendar = () => {
    const y = selectedDate.getFullYear();
    const m = selectedDate.getMonth();
    const firstDay = (new Date(y, m, 1).getDay() + 6) % 7;
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const cells: React.ReactNode[] = [];

    for (let i = 0; i < firstDay; i++) {
      cells.push(<div key={`e-${i}`} className="w-8 h-8" />);
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const dayDate = new Date(y, m, d);
      const key = toApiDate(dayDate);
      cells.push(
        <CustomDay
          key={d}
          day={d}
          hasRecording={recordedDayKeys.has(key)}
          isSelected={dayDate.toDateString() === selectedDate.toDateString()}
          onClick={(day) => setSelectedDate(new Date(y, m, day))}
        />,
      );
    }
    return cells;
  };

  return (
    <div className="flex flex-col h-full min-h-0 bg-gray-900 text-gray-300 overflow-hidden">
      <PageHeader
        title="Playback"
        subtitle={
          selectedFloor
            ? `${cameras.length} cameras — ${selectedFloor.location_path}`
            : 'Select a floor to browse recordings'
        }
      />
      <div className="px-4 pb-2 flex-shrink-0">
        {groupsLoading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 size={16} className="animate-spin" />
            Loading locations…
          </div>
        ) : (
          <LocationSelector
            buildings={buildings}
            selectedBuilding={selectedBuilding}
            selectedGroup={selectedGroup}
            onSelectBuilding={handleSelectBuilding}
            onSelectGroup={setSelectedGroup}
            allowAllLocations={hasUnrestrictedCameraAccess(cameraAccess)}
          />
        )}
      </div>
      <div className="grid flex-1 min-h-0 grid-cols-1 lg:grid-cols-[minmax(17rem,20rem)_1fr] gap-px bg-gray-700 overflow-hidden">
        {/* Left panel — cameras, calendar, sessions */}
        <div className="flex flex-col min-h-0 max-h-52 lg:max-h-none bg-gray-800 overflow-hidden border-r border-gray-700/50">
          <div className="flex-shrink-0 p-2.5 border-b border-gray-700">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
              <input
                type="text"
                placeholder="Search devices..."
                value={cameraFilter}
                onChange={(e) => setCameraFilter(e.target.value)}
                className="w-full pl-8 pr-2 py-1 bg-gray-700 border border-gray-600 rounded-lg text-xs text-gray-300 placeholder-gray-500 focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>

          <div className="max-h-36 lg:max-h-40 overflow-y-auto scrollbar-hide flex-shrink-0 border-b border-gray-700/50">
            <div className="px-2 py-1.5">
              {camerasLoading ? (
                <div className="flex items-center justify-center py-4 text-gray-500 text-xs">
                  <Loader2 size={14} className="animate-spin mr-2" />
                  Loading cameras…
                </div>
              ) : filteredCameras.length === 0 ? (
                <p className="text-center text-gray-500 text-[10px] py-4 px-1">
                  {selectedGroup ? 'No cameras in this floor' : 'Select a floor'}
                </p>
              ) : (
                filteredCameras.map((camera) => (
                    <button
                  key={camera.id}
                  type="button"
                  onClick={() => setSelectedCamera(camera)}
                  className={`flex items-center w-full text-left px-2 py-1.5 text-xs rounded-md mb-0.5 transition-all ${
                    selectedCamera?.id === camera.id
                          ? 'bg-blue-600/20 text-blue-300 border border-blue-500/30'
                      : 'text-gray-400 hover:text-white hover:bg-gray-700/50'
                  }`}
                >
                  <Video size={12} className="mr-1.5 text-blue-400 flex-shrink-0" />
                  <span className="truncate flex-grow">{camera.displayName || camera.name}</span>
                    </button>
                ))
              )}
            </div>
          </div>

          <div className="flex-shrink-0 p-2 border-t border-gray-700">
            <div className="flex items-center justify-center mb-2">
              <Calendar size={14} className="mr-1.5 text-gray-400" />
              <span className="text-xs text-gray-400 font-medium uppercase tracking-wide">Calendar</span>
            </div>
            <div className="p-2 bg-gray-700/80 rounded-lg border border-gray-600">
              <div className="flex justify-between items-center mb-2">
                <button
                  type="button"
                  onClick={() => setSelectedDate((d) => new Date(d.getFullYear(), d.getMonth() - 1, 1))}
                  className="text-blue-400 hover:text-white px-1"
                >
                  ‹
                </button>
                <span className="text-sm font-semibold text-white">
                  {selectedDate.toLocaleString('default', { month: 'short' })} {selectedDate.getFullYear()}
                </span>
                <button
                  type="button"
                  onClick={() => setSelectedDate((d) => new Date(d.getFullYear(), d.getMonth() + 1, 1))}
                  className="text-blue-400 hover:text-white px-1"
                >
                  ›
                </button>
              </div>
              <div className="grid grid-cols-7 gap-1 text-center text-xs text-gray-500 font-medium mb-1">
                {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((day, i) => (
                  <div key={i}>{day}</div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-1">{renderCalendar()}</div>
            <button
                type="button"
              onClick={handleSearch}
                disabled={isSearching || !selectedCamera}
                className="mt-2 w-full flex items-center justify-center py-2 text-xs font-semibold rounded-md bg-red-600 hover:bg-red-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isSearching ? (
                <>
                  <Loader2 size={14} className="mr-2 animate-spin" />
                  Searching...
                </>
              ) : (
                <>
                  <Search size={16} className="mr-2" />
                  Search Recordings
                </>
              )}
            </button>
          </div>
        </div>

          {renderSessionsList()}
        </div>

        {/* Player (top) + timeline & controls (pinned bottom) */}
        <div className="flex flex-col min-h-0 min-w-0 h-full bg-gray-900 overflow-hidden">
          <div
            ref={videoContainerRef}
            className="flex-1 min-h-0 relative w-full bg-black overflow-hidden"
          >
                  {selectedCamera ? (
                    <>
                          <video
                            ref={videoRef}
                            playsInline
                    className="absolute inset-0 w-full h-full object-contain"
                  />

                  {(isSearching || videoLoading) && (
                    <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-gray-900/80">
                      <Loader2 className="animate-spin text-blue-400 mb-2" size={32} />
                      <p className="text-xs text-gray-400">Loading...</p>
                    </div>
                  )}
                  {videoError && (
                    <div className="absolute top-14 left-4 right-4 z-10 bg-red-900/80 text-red-200 text-xs px-3 py-2 rounded">
                      {videoError}
                          </div>
                  )}
                  {gapNotice && (
                    <div
                      className={`absolute left-4 right-4 z-10 bg-amber-900/85 text-amber-100 text-xs px-3 py-2 rounded border border-amber-700/50 ${
                        activeRecording ? 'top-14' : 'top-1/2 -translate-y-1/2 text-center text-sm'
                      }`}
                    >
                      {gapNotice}
                        </div>
                  )}
                  {!activeRecording && !isSearching && !gapNotice && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500 z-[1] pointer-events-none bg-black/60">
                      <Video size={32} className="mb-1 opacity-30" />
                      <p className="text-[10px] text-center px-2 text-gray-500">
                        Search Recordings in calendar (left)
                      </p>
                    </div>
                  )}

                  <div className="absolute top-0 left-0 right-0 z-20 flex justify-between items-start p-2 bg-gradient-to-b from-black/80 to-transparent pointer-events-none">
                    <span className="bg-black/50 text-white text-xs px-2 py-0.5 rounded font-mono">
                      {selectedDate.toLocaleDateString()} {absoluteTimeLabel}
                    </span>
                    <span className="bg-black/50 text-white text-xs px-2 py-0.5 rounded">
                      {selectedCamera.displayName || selectedCamera.name}
                    </span>
                  </div>
                  <div className="absolute bottom-0 left-0 right-0 z-20 flex justify-between items-center px-2 py-1.5 bg-gradient-to-t from-black/80 to-transparent pointer-events-none">
                    <span className="text-blue-300 text-xs">{activeRecording ? 'Playback' : 'Idle'}</span>
                    <span className={`text-xs ${isPlaying ? 'text-green-400' : 'text-gray-400'}`}>
                      {isPlaying ? 'Playing' : 'Paused'}
                    </span>
                  </div>
                </>
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500 bg-gray-900">
                  <Video size={48} className="mb-4 opacity-40" />
                  <p className="text-sm">Select a camera to begin</p>
                </div>
              )}
          </div>

          <div className="flex-shrink-0 flex flex-col w-full px-2 pb-2 lg:px-3">
            <div className="flex flex-col w-full">
            <div className="w-full shrink-0 border-x border-gray-700 bg-gray-800">
          <PlaybackTimeline
            dateLabel={selectedDate.toLocaleDateString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
              year: 'numeric',
            })}
            selectedDate={selectedDate}
            recordings={playableRecordings}
            activeSessionId={activeSessionId}
            playheadPercent={playheadPercent}
            currentTimeLabel={absoluteTimeLabel}
            onBlockClick={(rec, dayPct) => {
              const full = recordings.find((r) => r.sessionId === rec.sessionId);
              if (!full) return;
              const offset = recordingSeekOffset(full, selectedDate, dayPct);
              playAt(full, offset);
            }}
            onGapClick={handleGapSelection}
          />
              </div>

          <div className="flex-shrink-0 w-full flex flex-wrap items-center justify-center gap-2 bg-gray-800 border border-t-0 border-gray-700 rounded-b-md px-2 py-1.5">
            <div className="flex items-center gap-1 bg-gray-700/50 rounded-lg p-1">
                  <button
                type="button"
                onClick={() => seek(Math.max(0, currentTime - 10))}
                disabled={!activeRecording}
                className="p-1.5 text-gray-400 hover:text-white disabled:opacity-40"
                title="Back 10s"
                  >
                    <ChevronsLeft size={16} />
                  </button>
                  <button
                type="button"
                onClick={togglePlayPause}
                disabled={!activeRecording || videoLoading}
                className="p-2 bg-blue-600 hover:bg-blue-500 text-white rounded-full disabled:opacity-40"
                  >
                    {isPlaying ? <Pause size={16} /> : <Play size={16} />}
                  </button>
                  <button
                type="button"
                onClick={() => seek(Math.min(effectiveDuration, currentTime + 10))}
                disabled={!activeRecording}
                className="p-1.5 text-gray-400 hover:text-white disabled:opacity-40"
                title="Forward 10s"
                  >
                    <ChevronsRight size={16} />
                  </button>
              </div>

            <div className="flex items-center gap-2 text-sm font-mono bg-gray-900/80 px-2 py-1 rounded border border-gray-700">
              <Clock size={14} className="text-gray-500" />
              <span className="text-red-400">{absoluteTimeLabel}</span>
              <span className="text-gray-600">|</span>
              <span className="text-gray-300">{formatClock(currentTime)}</span>
              <span className="text-gray-600">/</span>
              <span className="text-gray-400">{formatClock(effectiveDuration)}</span>
              </div>

            <div className="flex items-center gap-1">
              {([1, 2, 4] as const).map((speed) => (
                <button
                  key={speed}
                  type="button"
                  disabled={!activeRecording}
                  onClick={() => setPlaybackSpeed(speed)}
                  className={`px-2.5 py-1 rounded text-xs font-medium disabled:opacity-40 ${
                    playbackSpeed === speed
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-700 text-gray-400 hover:text-white'
                  }`}
                >
                  {speed}x
                </button>
              ))}
              </div>

            <button
              type="button"
              onClick={captureSnapshot}
              disabled={!activeRecording || videoLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs bg-gray-700/50 text-gray-300 hover:text-white hover:bg-gray-700 disabled:opacity-40"
              title="Capture picture"
            >
              <Camera size={16} />
              Capture
            </button>

            <button
              type="button"
              onClick={toggleFullscreen}
              disabled={!selectedCamera}
              className="p-2 text-gray-400 hover:text-white disabled:opacity-40"
              title="Fullscreen"
            >
              {isFullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
            </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
