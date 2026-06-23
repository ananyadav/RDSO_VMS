import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, ChevronLeft, ChevronRight, Move, Circle, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useLiveStream } from '../hooks/useLiveStream';
import type { FullscreenStartupPhase } from '../hooks/useLiveHLS';
import { CodecBadge } from './CodecBadge';
import CameraSelector from './CameraSelector';

interface Camera {
  id: string;
  name: string;
  online: boolean;
  ptz: boolean;
  activity: boolean;
}

interface FullscreenCameraModalProps {
  camera: Camera;
  allCameras: Camera[];
  onClose: () => void;
  onChangeCamera: (camera: Camera) => void;
  isRecording: boolean;
  onToggleRecording: (cameraId: string) => void;
}

function hlsStartupLabel(phase: FullscreenStartupPhase, forceSub: boolean): string {
  switch (phase) {
    case 'connecting_main':
      return 'Connecting to main stream 101';
    case 'waiting_playlist':
      return 'Waiting for playlist';
    case 'waiting_segment':
      return 'Waiting for first segment';
    case 'playing':
      return 'Playing';
    case 'failed_453':
      return 'Failed: RTSP 453 / camera limit';
    case 'fallback_sub':
      return forceSub ? 'Connecting to sub stream 102' : 'Fallback: Sub Stream / Lower Quality';
    case 'failed':
      return 'Stream failed';
    default:
      return 'Connecting…';
  }
}

export default function FullscreenCameraModal({
  camera,
  allCameras,
  onClose,
  onChangeCamera,
  isRecording,
  onToggleRecording,
}: FullscreenCameraModalProps) {
  const playerRef = useRef<HTMLDivElement>(null);
  const [forceSub, setForceSub] = useState(false);
  const [sessionKey, setSessionKey] = useState(0);

  const live = useLiveStream(camera, {
    playerContainerRef: playerRef,
    fullscreen: true,
    eager: true,
    forceSub,
    sessionKey,
    profile: forceSub ? 'sub' : 'main',
  });

  const {
    provider,
    videoRef,
    isConnecting,
    error,
    streamStatus,
    streamLabel,
    tileBadge,
    startupPhase,
    fullscreenTimedOut,
    jumpingToLive,
  } = live;

  const isGo2Rtc = provider === 'go2rtc';

  const [currentIndex, setCurrentIndex] = useState(
    allCameras.findIndex((c) => c.id === camera.id),
  );

  const handleRetry = useCallback(() => {
    setForceSub(false);
    setSessionKey((k) => k + 1);
  }, []);

  const handleUseLowQuality = useCallback(() => {
    setForceSub(true);
    setSessionKey((k) => k + 1);
  }, []);

  useEffect(() => {
    setForceSub(false);
    setSessionKey((k) => k + 1);
  }, [camera.id]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        const nextIndex = (currentIndex + 1) % allCameras.length;
        setCurrentIndex(nextIndex);
        onChangeCamera(allCameras[nextIndex]);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const prevIndex = (currentIndex - 1 + allCameras.length) % allCameras.length;
        setCurrentIndex(prevIndex);
        onChangeCamera(allCameras[prevIndex]);
      } else if (e.key === 'Home') {
        e.preventDefault();
        setCurrentIndex(0);
        onChangeCamera(allCameras[0]);
      } else if (e.key === 'End') {
        e.preventDefault();
        const last = allCameras.length - 1;
        setCurrentIndex(last);
        onChangeCamera(allCameras[last]);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [currentIndex, allCameras, onClose, onChangeCamera]);

  const handleNext = () => {
    const nextIndex = (currentIndex + 1) % allCameras.length;
    setCurrentIndex(nextIndex);
    onChangeCamera(allCameras[nextIndex]);
  };

  const handlePrevious = () => {
    const prevIndex = (currentIndex - 1 + allCameras.length) % allCameras.length;
    setCurrentIndex(prevIndex);
    onChangeCamera(allCameras[prevIndex]);
  };

  const handleSelectCamera = (selectedCamera: Camera) => {
    const selectedIndex = allCameras.findIndex((c) => c.id === selectedCamera.id);
    setCurrentIndex(selectedIndex);
    onChangeCamera(selectedCamera);
  };

  const showGo2RtcError = isGo2Rtc && streamStatus === 'error' && Boolean(error);
  const showHlsTimeout =
    !isGo2Rtc &&
    fullscreenTimedOut &&
    streamStatus !== 'playing' &&
    startupPhase !== 'playing';
  const showHlsError =
    !isGo2Rtc &&
    (streamStatus === 'error' || startupPhase === 'failed_453') &&
    Boolean(error) &&
    !showHlsTimeout;

  const statusLabel = isGo2Rtc
    ? streamStatus === 'playing'
      ? 'Playing · Main 101 · go2rtc'
      : streamStatus === 'error'
        ? 'Stream failed'
        : 'Connecting · Main 101 · go2rtc'
    : hlsStartupLabel(startupPhase, forceSub);

  return (
    <div className="fixed inset-0 z-50 bg-black bg-opacity-75 flex items-center justify-center">
      <div className="relative w-full h-full flex items-center justify-center">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 p-2 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors"
        >
          <X size={24} />
        </button>

        <button
          onClick={handlePrevious}
          className="absolute left-4 top-1/2 transform -translate-y-1/2 p-3 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors disabled:opacity-50"
          disabled={allCameras.length <= 1}
        >
          <ChevronLeft size={24} />
        </button>

        <button
          onClick={handleNext}
          className="absolute right-4 top-1/2 transform -translate-y-1/2 p-3 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors disabled:opacity-50"
          disabled={allCameras.length <= 1}
        >
          <ChevronRight size={24} />
        </button>

        <div className="relative w-full h-full max-w-full max-h-full">
          {camera.online ? (
            isGo2Rtc ? (
              <div
                key={`${camera.id}-${sessionKey}-${forceSub ? 'sub' : 'main'}`}
                ref={playerRef}
                className={`live-monitor-player w-full h-full bg-black ${showGo2RtcError ? 'opacity-0' : ''}`}
              />
            ) : videoRef ? (
              <video
                ref={videoRef as React.RefObject<HTMLVideoElement>}
                autoPlay
                playsInline
                muted
                controls={false}
                className={`live-monitor-player w-full h-full object-contain bg-black ${
                  error || showHlsTimeout ? 'opacity-0 pointer-events-none' : ''
                }`}
              />
            ) : null
          ) : (
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-500 bg-black">
              <div className="text-white">Camera Offline</div>
            </div>
          )}

          <div className="absolute top-0 left-0 right-0 p-4 flex justify-between items-start bg-gradient-to-b from-black/80 to-transparent">
            <div className="flex items-center space-x-2 min-w-0">
              {isRecording && (
                <div className="flex-shrink-0 flex items-center bg-red-600 text-white text-xs font-bold pl-1.5 pr-2 py-0.5 rounded-full">
                  <span className="rec-dot mr-1"></span>
                  <span>REC</span>
                </div>
              )}
              <h2 className="font-bold text-white text-xl truncate">{camera.name}</h2>
            </div>

            <div className="flex items-center space-x-2 flex-wrap justify-end gap-y-1">
              {!isGo2Rtc && (
                <CodecBadge badge={tileBadge === 'none' && error ? 'error' : tileBadge} />
              )}
              <span
                className={`px-3 py-1 text-sm font-semibold rounded-full ${
                  streamStatus === 'playing'
                    ? 'bg-green-900/80 text-green-100'
                    : streamStatus === 'error'
                      ? 'bg-red-900/90 text-red-100'
                      : 'bg-blue-900/80 text-blue-100'
                }`}
              >
                {isConnecting && streamStatus !== 'playing' && (
                  <Loader2 className="inline animate-spin mr-1" size={14} />
                )}
                {statusLabel}
              </span>

              <span
                className={`flex-shrink-0 px-3 py-1 text-sm font-semibold rounded-full ${
                  camera.online ? 'text-green-800 bg-green-200' : 'text-red-800 bg-red-200'
                }`}
              >
                {camera.online ? 'Online' : 'Offline'}
              </span>

              <div className="bg-gray-700 border border-gray-600 text-white rounded-md px-2 py-1">
                <CameraSelector
                  cameras={allCameras}
                  selected={allCameras[currentIndex]}
                  onSelect={handleSelectCamera}
                />
              </div>
            </div>
          </div>

          <div className="absolute inset-x-0 bottom-0 p-4 bg-gradient-to-t from-black/80 to-transparent flex flex-col items-center gap-3">
            {!isGo2Rtc &&
              !forceSub &&
              streamStatus !== 'fallback' &&
              startupPhase !== 'fallback_sub' && (
                <button
                  type="button"
                  onClick={handleUseLowQuality}
                  className="text-sm px-4 py-2 rounded-lg bg-amber-900/70 border border-amber-600/50 text-amber-100 hover:bg-amber-800/80 transition-colors"
                >
                  Fallback: Sub Stream / Lower Quality
                </button>
              )}
            <div className="flex items-center space-x-4">
              <button
                onClick={() => onToggleRecording(camera.id)}
                className={`flex items-center space-x-2 py-2 px-4 rounded transition-colors bg-black/30 backdrop-blur-sm ${
                  isRecording ? 'text-red-400' : 'text-gray-200 hover:bg-white/20'
                }`}
              >
                <Circle size={16} className={isRecording ? 'fill-current' : ''} />
                <span>{isRecording ? 'Stop' : 'Record'}</span>
              </button>

              {camera.ptz && (
                <Link
                  to={`/ptz/${camera.id}`}
                  className="flex items-center space-x-2 py-2 px-4 rounded bg-black/30 backdrop-blur-sm text-gray-200 hover:bg-white/20 transition-colors"
                >
                  <Move size={16} />
                  <span>PTZ</span>
                </Link>
              )}
            </div>
          </div>

          {isConnecting && !showGo2RtcError && !showHlsTimeout && !showHlsError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 pointer-events-none gap-2">
              <div className="flex items-center gap-2 text-white text-lg">
                <Loader2 className="animate-spin" size={22} />
                {statusLabel}
              </div>
            </div>
          )}

          {jumpingToLive && !isGo2Rtc && !showHlsTimeout && !showHlsError && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 pointer-events-none">
              <span className="px-4 py-2 rounded-lg bg-black/80 text-amber-200 text-sm font-medium">
                Jumping to live
              </span>
            </div>
          )}

          {showHlsTimeout && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-900/95 px-6 z-20">
              <div className="text-center max-w-md space-y-5">
                <p className="text-white text-lg font-medium">
                  High quality stream is taking longer than expected
                </p>
                <p className="text-gray-400 text-sm">{statusLabel}</p>
                <div className="flex flex-wrap justify-center gap-3">
                  <button
                    type="button"
                    onClick={handleRetry}
                    className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-500"
                  >
                    Retry
                  </button>
                  <button
                    type="button"
                    onClick={handleUseLowQuality}
                    className="px-4 py-2 rounded-lg bg-amber-700 text-white hover:bg-amber-600"
                  >
                    Use Low Quality Stream
                  </button>
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 rounded-lg bg-gray-700 text-white hover:bg-gray-600"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          )}

          {(showGo2RtcError || showHlsError) && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-900/95 px-6 z-20">
              <div className="text-center max-w-md space-y-5">
                <p className="text-white text-center text-sm max-w-xl leading-relaxed">{error}</p>
                <div className="flex flex-wrap justify-center gap-3">
                  <button
                    type="button"
                    onClick={handleRetry}
                    className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-500"
                  >
                    Retry
                  </button>
                  {!isGo2Rtc && startupPhase === 'failed_453' && (
                    <button
                      type="button"
                      onClick={handleUseLowQuality}
                      className="px-4 py-2 rounded-lg bg-amber-700 text-white hover:bg-amber-600"
                    >
                      Use Low Quality Stream
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 rounded-lg bg-gray-700 text-white hover:bg-gray-600"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="absolute bottom-4 left-4 bg-black/50 backdrop-blur-sm text-white px-3 py-2 rounded text-xs">
            <div className="text-gray-300 mb-1">Navigate:</div>
            <div>◀ ▶ Arrow keys • ESC Close</div>
            {isGo2Rtc && streamLabel && (
              <div className="text-gray-400 mt-1">Stream: {streamLabel}</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
