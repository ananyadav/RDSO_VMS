import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { apiFetch } from '../lib/api';
import { useParams, useHistory, Link } from 'react-router-dom';
import { useGo2RtcLive } from '../hooks/useGo2RtcLive';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import {
  fetchPtzCameras,
  fetchPtzPresets,
  ptzCheckStatus,
  ptzDeletePreset,
  ptzGotoPreset,
  ptzMove,
  ptzSetPreset,
  ptzStop,
  type PtzCamera,
  type PtzPreset,
} from '../lib/ptzApi';
import { ArrowLeft } from 'lucide-react';
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

interface Camera {
  id: string;
  name: string;
  camera_uid?: string;
  cameraUid?: string;
  ip_address?: string;
  online: boolean;
  ptz: boolean;
}

const PTZ = () => {
  const { cameraId } = useParams<{ cameraId: string }>();
  const history = useHistory();
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [camera, setCamera] = useState<Camera | null>(null);
  const [ptzCameras, setPtzCameras] = useState<PtzCamera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streamsReady, setStreamsReady] = useState(false);
  const [presets, setPresets] = useState<PtzPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(false);
  const [selectedPresetId, setSelectedPresetId] = useState<number | null>(null);
  const [speed, setSpeed] = useState(() => {
    const n = Number(initialStringParam(initialParams, 'speed', '2'));
    return n >= 1 && n <= 3 ? n : 2;
  });
  const [ptzReady, setPtzReady] = useState<boolean | null>(null);
  const videoContainerRef = useRef<HTMLDivElement>(null);
  const moveTokenRef = useRef(0);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  useEffect(() => {
    void waitForGo2RtcReady().then(setStreamsReady);
  }, []);

  const urlValues = useMemo(() => ({ speed: speed === 2 ? null : String(speed) }), [speed]);
  useUrlSync(hydratedRef, setParams, urlValues);

  const loadPresets = useCallback(async (id: string) => {
    setPresetsLoading(true);
    try {
      const list = await fetchPtzPresets(id);
      setPresets(list);
      setSelectedPresetId((prev) => {
        if (prev != null && list.some((p) => p.id === prev)) return prev;
        return list[0]?.id ?? 1;
      });
    } finally {
      setPresetsLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialize = async () => {
      let finishLoading = true;
      setLoading(true);
      setError(null);
      try {
        const ptzList = await fetchPtzCameras();
        setPtzCameras(ptzList);

        if (!cameraId) {
          const first = ptzList.find((c) => c.online) ?? ptzList[0];
          if (first) {
            // Keep the loading state visible while the redirected route loads
            // the selected camera. Otherwise "Camera not found" flashes briefly.
            finishLoading = false;
            history.replace(`/ptz/${first.id}`);
            return;
          }
          setError(
            ptzList.length
              ? 'No PTZ cameras available. Mark cameras as PTZ in Camera Management.'
              : 'No PTZ cameras configured. Enable "PTZ camera" when adding/editing a camera.',
          );
          setLoading(false);
          return;
        }

        const response = await apiFetch('/api/cameras');
        const cameras: Camera[] = await response.json();
        const selected = cameras.find((c) => c.id === cameraId) ?? null;
        if (!selected) {
          setError(`Camera not found.`);
          setCamera(null);
        } else if (!selected.ptz) {
          setError('This camera is not marked as PTZ. Enable it in Camera Management.');
          setCamera(selected);
        } else {
          setCamera(selected);
          const status = await ptzCheckStatus(cameraId);
          setPtzReady(Boolean(status.ok && status.supported));
          if (!status.ok) {
            toast.error(status.error || 'Could not verify PTZ on camera HTTP port');
          }
          await loadPresets(cameraId);
        }
      } catch {
        setError('Failed to load PTZ cameras.');
      } finally {
        if (finishLoading) setLoading(false);
      }
    };
    void initialize();
  }, [cameraId, history, loadPresets]);

  const { isConnecting, error: streamError, streamStatus } = useGo2RtcLive(camera, {
    containerRef: videoContainerRef,
    profile: 'main',
    // Wait until the loading screen is removed so the player container exists.
    active: Boolean(camera?.online && !loading),
    eager: true,
    streamsReady,
  });

  const handleMoveStart = useCallback(
    async (direction: string) => {
      if (!camera?.id) return;
      const token = ++moveTokenRef.current;
      if (direction === 'home') {
        await ptzStop(camera.id);
        return;
      }
      const result = await ptzMove(camera.id, direction, speed);
      if (token !== moveTokenRef.current) return;
      if (!result.ok) toast.error(result.error || 'PTZ move failed');
    },
    [camera?.id, speed],
  );

  const handleMoveStop = useCallback(async () => {
    moveTokenRef.current += 1;
    if (!camera?.id) return;
    await ptzStop(camera.id);
  }, [camera?.id]);

  const handleRecallPreset = async () => {
    if (!camera?.id || selectedPresetId == null) return;
    const result = await ptzGotoPreset(camera.id, selectedPresetId);
    if (result.ok) toast.success(`Recalled preset ${selectedPresetId}`);
    else toast.error(result.error || 'Recall failed');
  };

  const handleSetPreset = async () => {
    if (!camera?.id || selectedPresetId == null) return;
    const preset = presets.find((p) => p.id === selectedPresetId);
    const name = preset?.name || `Preset ${selectedPresetId}`;
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

  const controlsDisabled = !camera.online || ptzReady === false;
  const streamBusy = isConnecting || streamStatus === 'connecting';

  return (
    <div className="flex flex-col h-full bg-gray-900 text-gray-300">
      <div className="flex-shrink-0 px-4 pt-4">
        <Link to="/live" className="inline-flex items-center text-blue-400 hover:text-blue-300 mb-4">
          <ArrowLeft size={18} className="mr-2" /> Back to Live View
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold text-white">PTZ: {camera.name}</h1>
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
        {error && <p className="text-amber-400 text-sm mt-2">{error}</p>}
        {ptzReady === false && (
          <p className="text-amber-400 text-sm mt-2">
            PTZ ISAPI not responding on HTTP port 80 — check camera IP/credentials or set http_port on the camera.
          </p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-4">
          <div className="lg:col-span-2 bg-black rounded-md relative min-h-[360px]">
            <div ref={videoContainerRef} className="absolute inset-0 w-full h-full" />
            {(streamBusy || streamError) && (
              <div className="absolute inset-0 flex items-center justify-center text-white bg-black/60 z-10 px-4 text-center">
                {streamError ? `Stream: ${streamError}` : 'Connecting video…'}
              </div>
            )}
          </div>

          <div className="lg:col-span-1 flex flex-col gap-6">
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
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default PTZ;
