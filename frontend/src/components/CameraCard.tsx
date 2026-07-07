import React, { useRef } from 'react';
import { VideoOff, Circle, Maximize, Loader2 } from 'lucide-react';
import { useLiveStream } from '../hooks/useLiveStream';
import { CodecBadge } from './CodecBadge';
import { cameraTileLabel } from '../lib/cameraLabel';

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
  isRecording: boolean;
  onToggleRecording: (cameraId: string) => void;
  onFullscreen?: (camera: Camera) => void;
}

function CameraCard({
  camera,
  eagerLive = false,
  streamsReady = true,
  isRecording,
  onToggleRecording,
  onFullscreen,
}: CameraCardProps) {
  const tileRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<HTMLDivElement>(null);

  const {
    provider,
    videoRef,
    playerContainerRef,
    isConnecting,
    error,
    inView,
    tileBadge,
    jumpingToLive,
  } = useLiveStream(camera, {
    observeRef: tileRef,
    playerContainerRef: playerRef,
    eager: eagerLive,
    profile: 'sub',
    streamsReady,
  });

  const showStreamMessage = Boolean(error);
  const displayBadge = tileBadge === 'none' && error ? 'error' : tileBadge;
  const isGo2Rtc = provider === 'go2rtc';

  const handleDoubleClick = () => {
    if (onFullscreen) onFullscreen(camera);
  };

  return (
    <div
      className="group w-full h-full bg-white dark:bg-gray-900 overflow-hidden flex flex-col transition-all duration-300 relative ring-1 ring-gray-300 dark:ring-gray-700"
    >
      <div
        ref={tileRef}
        className="relative flex-1 min-h-0 bg-black cursor-pointer"
        onDoubleClick={handleDoubleClick}
      >
        <div className="absolute inset-0">
          {camera.online ? (
            <>
              {!inView && !isConnecting && !showStreamMessage && (
                <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-xs bg-gray-900 px-2 text-center z-10">
                  Scroll into view for live feed
                </div>
              )}

              {isGo2Rtc ? (
                <div
                  ref={playerContainerRef as React.RefObject<HTMLDivElement>}
                  className={`live-monitor-player absolute inset-0 ${
                    showStreamMessage ? 'opacity-0 pointer-events-none' : ''
                  }`}
                />
              ) : videoRef ? (
                <video
                  ref={videoRef as React.RefObject<HTMLVideoElement>}
                  autoPlay
                  playsInline
                  muted
                  controls={false}
                  className={`live-monitor-player absolute inset-0 w-full h-full object-cover ${
                    showStreamMessage ? 'opacity-0 pointer-events-none' : ''
                  }`}
                />
              ) : null}

              {isConnecting && !showStreamMessage && (
                <div className="absolute inset-0 animate-pulse bg-gray-800/60 z-10">
                  <div className="absolute inset-0 flex items-center justify-center">
                    <div className="flex items-center gap-2 text-gray-200 text-sm">
                      <Loader2 className="animate-spin" size={18} />
                      <span>Connecting…</span>
                    </div>
                  </div>
                </div>
              )}

              {jumpingToLive && !showStreamMessage && !isGo2Rtc && (
                <div className="absolute inset-x-0 bottom-2 flex justify-center z-20 pointer-events-none">
                  <span className="px-2 py-1 rounded bg-black/70 text-amber-200 text-xs font-medium">
                    Jumping to live
                  </span>
                </div>
              )}

              {showStreamMessage && (
                <div className="absolute inset-0 flex items-center justify-center text-gray-100 text-xs text-center px-3 bg-gray-900 z-20">
                  <p className="max-w-[95%] leading-relaxed">{error}</p>
                </div>
              )}
            </>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500">
              <VideoOff size={48} />
              <p>Camera Offline</p>
            </div>
          )}
        </div>

        <div className="absolute top-0 left-0 right-0 p-2 flex justify-between items-start bg-gradient-to-b from-black/60 to-transparent z-10">
          <div className="flex items-center space-x-2 min-w-0 flex-wrap gap-1">
            {isRecording && (
              <div className="flex-shrink-0 flex items-center bg-red-600 text-white text-xs font-bold pl-1.5 pr-2 py-0.5 rounded-full">
                <span className="rec-dot mr-1"></span>
                <span>REC</span>
              </div>
            )}
            <h3 className="font-bold text-white text-sm truncate">{cameraTileLabel(camera)}</h3>
            {camera.online && !isGo2Rtc && <CodecBadge badge={displayBadge} />}
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
            Double-click for fullscreen
          </div>
        </div>

        <div
          className="absolute inset-x-0 bottom-0 p-2 bg-gradient-to-t from-black/60 to-transparent
                     opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex justify-end z-10"
        >
          <div className="flex items-center space-x-2">
            {onFullscreen && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onFullscreen(camera);
                }}
                className="p-1.5 rounded text-gray-200 hover:bg-white/20 bg-black/30 backdrop-blur-sm transition-colors"
                title="Fullscreen"
              >
                <Maximize size={14} />
              </button>
            )}

            <button
              onClick={() => onToggleRecording(camera.id)}
              className={`flex items-center space-x-1.5 py-1 px-2 rounded transition-colors bg-black/30 backdrop-blur-sm ${
                isRecording ? 'text-red-400' : 'text-gray-200 hover:bg-white/20'
              }`}
            >
              <Circle size={12} className={isRecording ? 'fill-current' : ''} />
              <span>{isRecording ? 'Stop' : 'Record'}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default React.memo(CameraCard);
