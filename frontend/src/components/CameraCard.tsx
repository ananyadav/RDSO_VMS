import React, { useEffect, useRef, useState } from 'react';
import { VideoOff, Circle, Maximize, Loader2 } from 'lucide-react';
import { useGo2RtcLive } from '../hooks/useGo2RtcLive';
import { cameraTileLabel } from '../lib/cameraLabel';
import { isSuperAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';

/** Shared so virtualized tiles do not stampede /api/health. */
let recordingEngineEnabledCache: boolean | null = null;
let recordingEngineEnabledInflight: Promise<boolean> | null = null;

function readRecordingEngineEnabled(): Promise<boolean> {
  if (recordingEngineEnabledCache !== null) {
    return Promise.resolve(recordingEngineEnabledCache);
  }
  if (!recordingEngineEnabledInflight) {
    recordingEngineEnabledInflight = fetch('/api/health', { credentials: 'include' })
      .then((res) => res.json())
      .then((data: { enabled?: boolean; recording?: { enabled?: boolean } }) => {
        recordingEngineEnabledCache = Boolean(data?.enabled || data?.recording?.enabled);
        return recordingEngineEnabledCache;
      })
      .catch(() => {
        recordingEngineEnabledCache = false;
        return false;
      });
  }
  return recordingEngineEnabledInflight;
}

/** Record/Stop: SUPER_ADMIN only, and only while the recording engine is enabled. */
export function useShowManualRecordingControls(): boolean {
  const superAdmin = isSuperAdminUser(authService.getCurrentUser());
  const [engineEnabled, setEngineEnabled] = useState(
    () => recordingEngineEnabledCache === true,
  );

  useEffect(() => {
    if (!superAdmin) return;
    let cancelled = false;
    void readRecordingEngineEnabled().then((enabled) => {
      if (!cancelled) setEngineEnabled(enabled);
    });
    return () => {
      cancelled = true;
    };
  }, [superAdmin]);

  return superAdmin && engineEnabled;
}

interface Camera {
  id: string;
  name: string;
  displayName?: string;
  ip_address?: string;
  cameraUid?: string;
  online: boolean;
}

interface CameraCardProps {
  camera: Camera;
  eagerLive?: boolean;
  streamsReady?: boolean;
  /** Scroll root for visibility (live grid viewport). */
  observeRootRef?: React.RefObject<HTMLElement | null>;
  /** When false, tear down the tile player (e.g. same cam is in fullscreen). */
  liveActive?: boolean;
  isRecording: boolean;
  onToggleRecording: (cameraId: string) => void;
  onFullscreen?: (camera: Camera) => void;
  controlRoom?: boolean;
}

function CameraCard({
  camera,
  eagerLive = false,
  streamsReady = true,
  observeRootRef,
  liveActive = true,
  isRecording,
  onToggleRecording,
  onFullscreen,
  controlRoom = false,
}: CameraCardProps) {
  const tileRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const showManualRecordingControls = useShowManualRecordingControls();

  const { isConnecting, isQueued, streamStatus, inView } = useGo2RtcLive(
    camera.online ? camera : null,
    {
      containerRef: playerRef,
      observeRef: tileRef,
      observeRootRef,
      profile: 'sub',
      eager: eagerLive,
      streamEligible: eagerLive,
      active: liveActive && camera.online,
      streamsReady,
    },
  );

  const showConnecting =
    camera.online &&
    streamStatus !== 'playing' &&
    (isQueued || isConnecting || !streamsReady || (eagerLive && !inView));

  const handleDoubleClick = () => {
    if (onFullscreen) onFullscreen(camera);
  };

  return (
    <div
      className={`w-full h-full overflow-hidden flex flex-col relative group ${
        controlRoom
          ? 'bg-black'
          : 'bg-white dark:bg-gray-900 transition-all duration-300 ring-1 ring-gray-300 dark:ring-gray-700'
      }`}
      data-live-stream-eligible={eagerLive ? 'true' : 'false'}
      data-live-stream-status={streamStatus}
      data-live-stream-queued={isQueued ? 'true' : 'false'}
    >
      <div
        ref={tileRef}
        className="relative flex-1 min-h-0 bg-black cursor-pointer"
        onDoubleClick={handleDoubleClick}
      >
        <div className="absolute inset-0">
          {camera.online ? (
            <>
              <div ref={playerRef} className="live-monitor-player absolute inset-0" />

              {!controlRoom && showConnecting && (
                <div className="absolute inset-0 animate-pulse bg-gray-800/60 z-10">
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="flex items-center gap-2 text-gray-200 text-sm">
                      <Loader2 className="animate-spin" size={18} />
                      <span>Connecting…</span>
                    </div>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500">
              {!controlRoom && (
                <>
                  <VideoOff size={48} />
                  <p>Camera Offline</p>
                </>
              )}
            </div>
          )}
        </div>

        {!controlRoom && (
          <>
        <div className="absolute top-0 left-0 right-0 p-2 flex justify-between items-start bg-gradient-to-b from-black/60 to-transparent z-10">
          <div className="flex items-center space-x-2 min-w-0 flex-wrap gap-1">
            {isRecording && (
              <div className="flex-shrink-0 flex items-center bg-red-600 text-white text-xs font-bold pl-1.5 pr-2 py-0.5 rounded-full">
                <span className="rec-dot mr-1"></span>
                <span>REC</span>
              </div>
            )}
            <h3 className="font-bold text-white text-sm truncate">{cameraTileLabel(camera)}</h3>
          </div>

          <span
            className={`flex-shrink-0 px-2 py-0.5 text-xs font-semibold rounded-full ${
              camera.online ? 'text-green-800 bg-green-200' : 'text-red-800 bg-red-200'
            }`}
          >
            {camera.online ? 'Online' : 'Offline'}
          </span>
        </div>

        <div
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
                     opacity-0 group-hover:opacity-100 transition-opacity duration-300 z-10"
        >
          <div className="bg-black/50 backdrop-blur-sm text-white px-2 py-1 rounded text-xs">
            Double-click to fullscreen / exit
          </div>
        </div>
          </>
        )}

        {(onFullscreen || (!controlRoom && showManualRecordingControls)) && (
          <div
            className={`absolute z-20 ${
              controlRoom
                ? 'bottom-1 right-1 opacity-80 group-hover:opacity-100'
                : 'inset-x-0 bottom-0 p-2 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex justify-end'
            }`}
          >
            <div className="flex items-center space-x-2">
              {onFullscreen && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onFullscreen(camera);
                  }}
                  className="p-1.5 rounded text-gray-200 hover:bg-white/20 bg-black/50 backdrop-blur-sm transition-colors"
                  title="Fullscreen"
                  aria-label={`Fullscreen ${cameraTileLabel(camera)}`}
                >
                  <Maximize size={14} />
                </button>
              )}

              {!controlRoom && showManualRecordingControls && (
                <button
                  onClick={() => onToggleRecording(camera.id)}
                  className={`flex items-center space-x-1.5 py-1 px-2 rounded transition-colors bg-black/30 backdrop-blur-sm ${
                    isRecording ? 'text-red-400' : 'text-gray-200 hover:bg-white/20'
                  }`}
                >
                  <Circle size={12} className={isRecording ? 'fill-current' : ''} />
                  <span>{isRecording ? 'Stop' : 'Record'}</span>
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default React.memo(CameraCard);
