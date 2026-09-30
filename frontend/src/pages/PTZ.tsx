import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useParams, useHistory, Link } from 'react-router-dom';
import { useGo2RtcLive } from '../hooks/useGo2RtcLive';
import { usePtzHoldControl } from '../hooks/usePtzHoldControl';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import {
  fetchPtzCameras,
  fetchPtzPatterns,
  fetchPtzPresets,
  fetchPtzTours,
  ptzCheckStatus,
  ptzDeletePattern,
  ptzDeletePreset,
  ptzDeleteTour,
  ptzGotoPreset,
  ptzRecordPatternStart,
  ptzRecordPatternStop,
  ptzSetPattern,
  ptzSetPreset,
  ptzSetTour,
  ptzStartPattern,
  ptzStartTour,
  ptzStopPattern,
  ptzStopTour,
  type PtzCamera,
  type PtzCapabilities,
  type PtzPattern,
  type PtzPreset,
  type PtzTour,
  type PtzTourStep,
} from '../lib/ptzApi';
import toast from 'react-hot-toast';
import { authService } from '../services/authService';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import {
  useUrlHydration,
  useUrlSync,
  initialStringParam,
} from '../hooks/useUrlSearchState';

import PTZControls from '../components/PTZControls';
import PTZPresets from '../components/PTZPresets';
import PTZTours from '../components/PTZTours';
import PTZPatterns from '../components/PTZPatterns';

interface Camera {
  id: string;
  name: string;
  camera_uid?: string;
  cameraUid?: string;
  ip_address?: string;
  online: boolean;
  ptz: boolean;
  workerId?: number | string | null;
}

const PTZ = () => {
  const { cameraId } = useParams<{ cameraId: string }>();
  const history = useHistory();
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [camera, setCamera] = useState<Camera | null>(null);
  const [ptzCameras, setPtzCameras] = useState<PtzCamera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streamsReady, setStreamsReady] = useState(true);
  const [presets, setPresets] = useState<PtzPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(false);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<number | null>(1);
  const [tours, setTours] = useState<PtzTour[]>([]);
  const [toursLoading, setToursLoading] = useState(false);
  const [toursError, setToursError] = useState<string | null>(null);
  const [selectedTourId, setSelectedTourId] = useState<number | null>(1);
  const [patterns, setPatterns] = useState<PtzPattern[]>([]);
  const [patternsLoading, setPatternsLoading] = useState(false);
  const [patternsError, setPatternsError] = useState<string | null>(null);
  const [selectedPatternId, setSelectedPatternId] = useState<number | null>(1);
  const [capabilities, setCapabilities] = useState<PtzCapabilities | null>(null);
  const [speed, setSpeed] = useState(() => {
    const n = Number(initialStringParam(initialParams, 'speed', '2'));
    return n >= 1 && n <= 3 ? n : 2;
  });
  const [streamSession, setStreamSession] = useState(0);
  const videoContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  useEffect(() => {
    void waitForGo2RtcReady().then(setStreamsReady);
  }, []);

  const urlValues = useMemo(() => ({ speed: speed === 2 ? null : String(speed) }), [speed]);
  useUrlSync(hydratedRef, setParams, urlValues);

  const loadCapabilities = useCallback(async (id: string) => {
    const caps = await ptzCheckStatus(id);
    setCapabilities(caps);
    return caps;
  }, []);

  const loadPresets = useCallback(async (id: string) => {
    setPresetsLoading(true);
    setPresetsError(null);
    try {
      const result = await fetchPtzPresets(id);
      if (!result.ok) {
        setPresets([]);
        setPresetsError(result.error || 'Failed to load presets');
        return result;
      }
      setPresets(result.presets);
      setSelectedPresetId((prev) => {
        if (prev != null && result.presets.some((p) => p.id === prev)) return prev;
        return result.presets[0]?.id ?? 1;
      });
      return result;
    } finally {
      setPresetsLoading(false);
    }
  }, []);

  const loadTours = useCallback(async (id: string) => {
    setToursLoading(true);
    setToursError(null);
    try {
      const result = await fetchPtzTours(id);
      if (!result.ok) {
        setTours([]);
        if (result.supported === false) {
          setToursError(null);
        } else {
          setToursError(result.error || 'Failed to load tours');
        }
        return result;
      }
      setTours(result.tours);
      setSelectedTourId((prev) => {
        if (prev != null && result.tours.some((t) => t.id === prev)) return prev;
        return result.tours[0]?.id ?? 1;
      });
      return result;
    } finally {
      setToursLoading(false);
    }
  }, []);

  const loadPatterns = useCallback(async (id: string) => {
    setPatternsLoading(true);
    setPatternsError(null);
    try {
      const result = await fetchPtzPatterns(id);
      if (!result.ok) {
        setPatterns([]);
        if (result.supported === false) {
          setPatternsError(null);
        } else {
          setPatternsError(result.error || 'Failed to load patterns');
        }
        return result;
      }
      setPatterns(result.patterns);
      setSelectedPatternId((prev) => {
        if (prev != null && result.patterns.some((p) => p.id === prev)) return prev;
        return result.patterns[0]?.id ?? 1;
      });
      return result;
    } finally {
      setPatternsLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialize = async () => {
      let finishLoading = true;
      setLoading(true);
      setError(null);
      setPresets([]);
      setTours([]);
      setPatterns([]);
      setCapabilities(null);
      setSelectedPresetId(1);
      setSelectedTourId(1);
      setSelectedPatternId(1);
      try {
        const ptzList = await fetchPtzCameras();
        setPtzCameras(ptzList);

        if (!cameraId) {
          const first = ptzList.find((c) => c.online) ?? ptzList[0];
          if (first) {
            finishLoading = false;
            history.replace(`/ptz/${first.id}`);
            return;
          }
          setError(
            ptzList.length
              ? 'No PTZ cameras available. Mark cameras as PTZ in Camera Management.'
              : 'No PTZ cameras configured. Enable "PTZ camera" when adding/editing a camera.',
          );
          setCamera(null);
          return;
        }

        const selected = ptzList.find((c) => c.id === cameraId) ?? null;
        if (!selected) {
          setError(
            'This camera is not marked as PTZ. Enable "PTZ camera" in Camera Management.',
          );
          setCamera(null);
          return;
        }

        setStreamSession((k) => k + 1);
        setCamera({
          id: selected.id,
          name: selected.name,
          cameraUid: selected.cameraUid,
          workerId: Number(selected.workerId) > 0 ? Number(selected.workerId) : 1,
          ip_address: selected.ip_address,
          online: selected.online !== false,
          ptz: true,
        });

        await Promise.all([
          loadCapabilities(selected.id),
          loadPresets(selected.id),
          loadTours(selected.id),
          loadPatterns(selected.id),
        ]);
      } catch {
        setError('Failed to load PTZ cameras.');
        setCamera(null);
      } finally {
        if (finishLoading) setLoading(false);
      }
    };
    void initialize();
  }, [cameraId, history, loadCapabilities, loadPresets, loadTours, loadPatterns]);

  const { isConnecting, error: streamError, streamStatus } = useGo2RtcLive(camera, {
    containerRef: videoContainerRef,
    profile: 'sub',
    active: Boolean(camera && !loading),
    eager: true,
    streamsReady,
    sessionKey: streamSession,
    background: true,
    maxPostPlayRetries: 1,
  });

  const {
    onMoveStart: handleMoveStart,
    onMoveStop: handleMoveStop,
    disabled: ptzDisabled,
  } = usePtzHoldControl(camera?.id, {
    enabled: Boolean(camera?.ptz),
    online: camera?.online !== false,
    speed,
  });

  const handleRecallPreset = async () => {
    if (!camera?.id || selectedPresetId == null) return;
    const result = await ptzGotoPreset(camera.id, selectedPresetId);
    if (result.ok) toast.success(`Recalled preset ${selectedPresetId}`);
    else toast.error(result.error || 'Recall failed');
  };

  const handleSetPreset = async (name: string) => {
    if (!camera?.id || selectedPresetId == null) return;
    const result = await ptzSetPreset(camera.id, selectedPresetId, name);
    if (result.ok) {
      toast.success(`Saved preset ${selectedPresetId}`);
      await loadPresets(camera.id);
    } else {
      toast.error(result.error || 'Set preset failed');
    }
  };

  const handleRemovePreset = async () => {
    if (!camera?.id || selectedPresetId == null) return;
    const result = await ptzDeletePreset(camera.id, selectedPresetId);
    if (result.ok) {
      toast.success(`Removed preset ${selectedPresetId}`);
      await loadPresets(camera.id);
    } else {
      toast.error(result.error || 'Remove preset failed');
    }
  };

  const handleSaveTour = async (tourId: number, name: string, steps: PtzTourStep[]) => {
    if (!camera?.id) return;
    const result = await ptzSetTour(camera.id, tourId, { name, steps, enabled: true });
    if (result.ok) {
      toast.success(`Saved tour ${tourId}`);
      await loadTours(camera.id);
    } else {
      toast.error(result.error || 'Save tour failed');
    }
  };

  const handleDeleteTour = async () => {
    if (!camera?.id || selectedTourId == null) return;
    const result = await ptzDeleteTour(camera.id, selectedTourId);
    if (result.ok) {
      toast.success(`Deleted tour ${selectedTourId}`);
      await loadTours(camera.id);
    } else {
      toast.error(result.error || 'Delete tour failed');
    }
  };

  const handleStartTour = async () => {
    if (!camera?.id || selectedTourId == null) return;
    const result = await ptzStartTour(camera.id, selectedTourId);
    if (result.ok) toast.success(`Started tour ${selectedTourId}`);
    else toast.error(result.error || 'Start tour failed');
  };

  const handleStopTour = async () => {
    if (!camera?.id || selectedTourId == null) return;
    const result = await ptzStopTour(camera.id, selectedTourId);
    if (result.ok) toast.success(`Stopped tour ${selectedTourId}`);
    else toast.error(result.error || 'Stop tour failed');
  };

  const handleSavePattern = async (patternId: number, name: string) => {
    if (!camera?.id) return;
    const result = await ptzSetPattern(camera.id, patternId, name);
    if (result.ok) {
      toast.success(`Saved pattern ${patternId}`);
      await loadPatterns(camera.id);
    } else {
      toast.error(result.error || 'Save pattern failed');
    }
  };

  const handleDeletePattern = async () => {
    if (!camera?.id || selectedPatternId == null) return;
    const result = await ptzDeletePattern(camera.id, selectedPatternId);
    if (result.ok) {
      toast.success(`Deleted pattern ${selectedPatternId}`);
      await loadPatterns(camera.id);
    } else {
      toast.error(result.error || 'Delete pattern failed');
    }
  };

  const handleStartPattern = async () => {
    if (!camera?.id || selectedPatternId == null) return;
    const result = await ptzStartPattern(camera.id, selectedPatternId);
    if (result.ok) toast.success(`Started pattern ${selectedPatternId}`);
    else toast.error(result.error || 'Start pattern failed');
  };

  const handleStopPattern = async () => {
    if (!camera?.id || selectedPatternId == null) return;
    const result = await ptzStopPattern(camera.id, selectedPatternId);
    if (result.ok) toast.success(`Stopped pattern ${selectedPatternId}`);
    else toast.error(result.error || 'Stop pattern failed');
  };

  const handleRecordPatternStart = async () => {
    if (!camera?.id || selectedPatternId == null) return;
    const result = await ptzRecordPatternStart(camera.id, selectedPatternId);
    if (result.ok) toast.success(`Recording pattern ${selectedPatternId} — move PTZ, then End rec`);
    else toast.error(result.error || 'Record start failed');
  };

  const handleRecordPatternStop = async () => {
    if (!camera?.id || selectedPatternId == null) return;
    const result = await ptzRecordPatternStop(camera.id, selectedPatternId);
    if (result.ok) {
      toast.success(`Finished recording pattern ${selectedPatternId}`);
      await loadPatterns(camera.id);
    } else {
      toast.error(result.error || 'Record stop failed');
    }
  };

  const handleCameraSwitch = (id: string) => {
    history.push(`/ptz/${id}`);
  };

  if (loading) {
    return <div className="text-white text-center p-8">Loading PTZ…</div>;
  }

  if (error && !camera) {
    return (
      <div className="flex flex-col items-center gap-4 text-center p-8">
        <p className="text-red-400">{error}</p>
        {hasPermission(authService.getCurrentUser(), PERMISSIONS.CAMERAS) && (
          <Link to="/camera-management" className="text-blue-400 hover:underline">
            Open Camera Management
          </Link>
        )}
      </div>
    );
  }

  if (!camera) {
    return <div className="text-red-400 text-center p-8">Camera not found.</div>;
  }

  const controlsDisabled = ptzDisabled;
  const streamBusy = isConnecting || streamStatus === 'connecting';
  const presetsSupported = capabilities?.presetsSupported !== false && capabilities?.presets?.list !== false;
  const toursSupported =
    capabilities?.toursSupported === true ||
    capabilities?.tours?.list === true ||
    (capabilities == null && tours.length > 0);
  const patternsSupported =
    capabilities?.patternsSupported === true ||
    capabilities?.patterns?.list === true ||
    (capabilities == null && patterns.length > 0);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-gray-900 text-gray-300">
      <div className="flex-shrink-0 px-4 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-xl font-bold text-white leading-tight">PTZ: {camera.name}</h1>
          <div className="flex items-center gap-2 flex-wrap">
            {ptzCameras.length > 1 && (
              <select
                value={camera.id}
                onChange={(e) => handleCameraSwitch(e.target.value)}
                className="select-style text-sm"
              >
                {ptzCameras.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.displayName || c.name} {c.online ? '' : '(offline)'}
                  </option>
                ))}
              </select>
            )}
            <div className="flex gap-1">
              {[1, 2, 3].map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSpeed(s)}
                  title={s === 1 ? 'Slow' : s === 3 ? 'Fast' : 'Medium'}
                  className={`px-3 py-1 rounded text-sm font-medium ${
                    speed === s ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
        {error && <p className="text-amber-400 text-sm mt-1">{error}</p>}
        {capabilities?.backend && (
          <p className="text-xs text-gray-500 mt-1">
            Protocol: {capabilities.backend}
            {capabilities.presetsSupported === false ? ' · presets unsupported' : ''}
            {capabilities.toursSupported === false ? ' · tours unsupported' : ''}
            {capabilities.patternsSupported === false ? ' · patterns unsupported' : ''}
          </p>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto lg:overflow-hidden grid grid-cols-1 lg:grid-cols-3 gap-3 px-4 pb-3">
        <div className="lg:col-span-2 bg-black rounded-md relative min-h-[240px] lg:min-h-0 overflow-hidden">
          <div ref={videoContainerRef} className="absolute inset-0 w-full h-full" />
          {(streamBusy || streamError) && (
            <div className="absolute inset-0 flex items-center justify-center text-white bg-black/60 z-10 px-4 text-center">
              {streamError ? `Stream: ${streamError}` : 'Connecting video…'}
              </div>
          )}
          </div>

        <div className="lg:col-span-1 min-h-0 overflow-y-auto flex flex-col gap-3">
          <PTZControls
            speed={speed}
            disabled={controlsDisabled}
            onMoveStart={handleMoveStart}
            onMoveStop={handleMoveStop}
          />
            <PTZPresets
              presets={presets}
            selectedPresetId={selectedPresetId}
            onPresetChange={setSelectedPresetId}
              onRecall={handleRecallPreset}
            onSet={handleSetPreset}
            onRemove={handleRemovePreset}
            disabled={controlsDisabled}
            loading={presetsLoading}
            supported={presetsSupported}
            error={presetsError}
            canSet={capabilities?.presets?.set !== false}
            canGoto={capabilities?.presets?.goto !== false}
            canDelete={capabilities?.presets?.delete !== false}
          />
          <PTZTours
            tours={tours}
            presets={presets}
            selectedTourId={selectedTourId}
            onTourChange={setSelectedTourId}
            onSave={handleSaveTour}
            onDelete={handleDeleteTour}
            onStart={handleStartTour}
            onStop={handleStopTour}
            disabled={controlsDisabled}
            loading={toursLoading}
            supported={Boolean(toursSupported)}
            error={toursError}
            canSet={capabilities?.tours?.set !== false}
            canStart={capabilities?.tours?.start !== false}
            canStop={capabilities?.tours?.stop !== false}
            canDelete={capabilities?.tours?.delete !== false}
          />
          <PTZPatterns
            patterns={patterns}
            selectedPatternId={selectedPatternId}
            onPatternChange={setSelectedPatternId}
            onSaveName={handleSavePattern}
            onDelete={handleDeletePattern}
            onStart={handleStartPattern}
            onStop={handleStopPattern}
            onRecordStart={handleRecordPatternStart}
            onRecordStop={handleRecordPatternStop}
            disabled={controlsDisabled}
            loading={patternsLoading}
            supported={Boolean(patternsSupported)}
            error={patternsError}
            canSet={capabilities?.patterns?.set === true}
            canStart={capabilities?.patterns?.start !== false}
            canStop={capabilities?.patterns?.stop !== false}
            canRecord={capabilities?.patterns?.record === true}
            canDelete={capabilities?.patterns?.delete === true}
          />
        </div>
      </div>
    </div>
  );
};

export default PTZ;
