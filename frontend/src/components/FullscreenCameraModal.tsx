import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, ChevronLeft, ChevronRight, Circle, Loader2 } from 'lucide-react';
import { useGo2RtcLive } from '../hooks/useGo2RtcLive';
import CameraSelector from './CameraSelector';
import LivePtzPad from './LivePtzPad';
import DigitalZoomControls from './DigitalZoomControls';
import VideoTitleTimeOverlay from './VideoTitleTimeOverlay';
import { useShowManualRecordingControls } from './CameraCard';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  INITIAL_DIGITAL_ZOOM,
  digitalZoomCssTransform,
  panDigitalZoom,
  resetDigitalZoom,
  zoomDigitalIn,
  zoomDigitalOut,
  type DigitalZoomState,
} from '../lib/digitalZoom';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import { authService } from '../services/authService';
import { formatOccurredAt, severityBadgeClass, sourceTypeLabel } from '../lib/eventLabels';
import type { AlarmEvent } from '../lib/eventsApi';

interface Camera {
  id: string;
  name: string;
  displayName?: string;
  ip_address?: string;
  cameraUid?: string;
  online: boolean;
  ptz?: boolean;
}

export type AlarmDisplayBanner = {
  event: AlarmEvent;
  queueCount?: number;
  onManualReset?: () => void;
  onAcknowledge?: () => void;
};

interface FullscreenCameraModalProps {
  camera: Camera;
  allCameras: Camera[];
  onClose: () => void;
  onChangeCamera: (camera: Camera) => void;
  isRecording: boolean;
  onToggleRecording: (cameraId: string) => void;
  /** RDSO 18.1.25 — alarmed camera overlay (reuses this modal's go2rtc path). */
  alarmBanner?: AlarmDisplayBanner | null;
}

export default function FullscreenCameraModal({
  camera,
  allCameras,
  onClose,
  onChangeCamera,
  isRecording,
  onToggleRecording,
  alarmBanner = null,
}: FullscreenCameraModalProps) {
  const playerRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    lastX: number;
    lastY: number;
    moved: boolean;
  } | null>(null);
  const [forceSub, setForceSub] = useState(false);
  const [sessionKey, setSessionKey] = useState(0);
  const [digitalZoom, setDigitalZoom] = useState<DigitalZoomState>(INITIAL_DIGITAL_ZOOM);
  const showManualRecordingControls = useShowManualRecordingControls();
  const canLiveView = hasPermission(authService.getCurrentUser(), PERMISSIONS.LIVE_VIEW);
  const showLivePtz = Boolean(camera.ptz) && canLiveView;
  /** Digital zoom for fixed + PTZ — never drives optical PTZ. */
  const showDigitalZoom = canLiveView;

  const profile = forceSub ? 'sub' : 'main';
  const { isConnecting, error, streamStatus, streamName } = useGo2RtcLive(camera, {
    containerRef: playerRef,
    profile,
    eager: true,
    sessionKey,
  });

  const [currentIndex, setCurrentIndex] = useState(
    allCameras.findIndex((c) => c.id === camera.id),
  );

  const viewportSize = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return { w: 0, h: 0 };
    return { w: el.clientWidth, h: el.clientHeight };
  }, []);

  const handleDigitalZoomIn = useCallback(() => {
    const { w, h } = viewportSize();
    setDigitalZoom((z) => zoomDigitalIn(z, w, h));
  }, [viewportSize]);

  const handleDigitalZoomOut = useCallback(() => {
    const { w, h } = viewportSize();
    setDigitalZoom((z) => zoomDigitalOut(z, w, h));
  }, [viewportSize]);

  const handleDigitalZoomReset = useCallback(() => {
    setDigitalZoom(resetDigitalZoom());
  }, []);

  const handleRetry = useCallback(() => {
    setForceSub(false);
    setSessionKey((k) => k + 1);
  }, []);

  const handleUseLowQuality = useCallback(() => {
    setForceSub(true);
    setSessionKey((k) => k + 1);
  }, []);

  // Auto-fallback to sub when main fails (common for HEVC / busy RTSP slots).
  useEffect(() => {
    if (forceSub) return;
    if (streamStatus !== 'error') return;
    setForceSub(true);
    setSessionKey((k) => k + 1);
  }, [forceSub, streamStatus]);

  // Reset digital zoom when camera changes (or modal remounts on close/open).
  useEffect(() => {
    setForceSub(false);
    setSessionKey((k) => k + 1);
    setDigitalZoom(resetDigitalZoom());
    dragRef.current = null;
  }, [camera.id]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
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
      } else if (e.key === '+' || e.key === '=') {
        e.preventDefault();
        handleDigitalZoomIn();
      } else if (e.key === '-' || e.key === '_') {
        e.preventDefault();
        handleDigitalZoomOut();
      } else if (e.key === '0' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        handleDigitalZoomReset();
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [
    currentIndex,
    allCameras,
    onClose,
    onChangeCamera,
    handleDigitalZoomIn,
    handleDigitalZoomOut,
    handleDigitalZoomReset,
  ]);

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

  const handleDoubleClickExit = (e: React.MouseEvent) => {
    e.preventDefault();
    onClose();
  };

  const stopDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
  };

  // Native non-passive wheel so preventDefault actually blocks page scroll.
  useEffect(() => {
    const el = viewportRef.current;
    if (!el || !showDigitalZoom) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (e.deltaY < 0) setDigitalZoom((z) => zoomDigitalIn(z, w, h));
      else setDigitalZoom((z) => zoomDigitalOut(z, w, h));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [showDigitalZoom, camera.id]);

  const handleViewportPointerDown = useCallback(    (e: React.PointerEvent) => {
      if (!showDigitalZoom || digitalZoom.scale <= 1) return;
      if (e.button !== 0) return;
      dragRef.current = {
        pointerId: e.pointerId,
        lastX: e.clientX,
        lastY: e.clientY,
        moved: false,
      };
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    },
    [digitalZoom.scale, showDigitalZoom],
  );

  const handleViewportPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const dx = e.clientX - drag.lastX;
      const dy = e.clientY - drag.lastY;
      if (dx === 0 && dy === 0) return;
      drag.lastX = e.clientX;
      drag.lastY = e.clientY;
      drag.moved = true;
      const { w, h } = viewportSize();
      setDigitalZoom((z) => panDigitalZoom(z, dx, dy, w, h));
    },
    [viewportSize],
  );

  const endViewportDrag = useCallback((e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    dragRef.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    } catch {
      /* ignore */
    }
  }, []);

  const showError = streamStatus === 'error' && Boolean(error);
  const channelLabel = forceSub ? '102 · sub' : '101 · main';
  const statusLabel =
    streamStatus === 'playing'
      ? `Playing · ${channelLabel} · go2rtc`
      : streamStatus === 'error'
        ? 'Stream failed'
        : `Connecting · ${channelLabel} · go2rtc`;
  const zoomCss = digitalZoomCssTransform(digitalZoom);
  const canPan = showDigitalZoom && digitalZoom.scale > 1;

  return (
    <div
      className="fixed inset-0 z-50 bg-black bg-opacity-75 flex items-center justify-center"
      onDoubleClick={handleDoubleClickExit}
    >
      <div className="relative w-full h-full flex items-center justify-center">
        <button
          onClick={onClose}
          onDoubleClick={stopDoubleClick}
          className="absolute top-4 right-4 z-10 p-2 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors"
        >
          <X size={24} />
        </button>

        <button
          onClick={handlePrevious}
          onDoubleClick={stopDoubleClick}
          className="absolute left-4 top-1/2 transform -translate-y-1/2 p-3 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors disabled:opacity-50"
          disabled={allCameras.length <= 1}
        >
          <ChevronLeft size={24} />
        </button>

        <button
          onClick={handleNext}
          onDoubleClick={stopDoubleClick}
          className="absolute right-4 top-1/2 transform -translate-y-1/2 p-3 bg-black/30 backdrop-blur-sm text-white rounded-full hover:bg-black/50 transition-colors disabled:opacity-50"
          disabled={allCameras.length <= 1}
        >
          <ChevronRight size={24} />
        </button>

        <div className="relative w-full h-full max-w-full max-h-full">
          {camera.online ? (
            <div
              ref={viewportRef}
              className={`relative w-full h-full overflow-hidden bg-black ${
                canPan ? 'cursor-grab active:cursor-grabbing' : ''
              }`}
              data-testid="digital-zoom-viewport"
              onPointerDown={handleViewportPointerDown}
              onPointerMove={handleViewportPointerMove}
              onPointerUp={endViewportDrag}
              onPointerCancel={endViewportDrag}
            >
              <div
                key={`${camera.id}-${sessionKey}-${profile}`}
                ref={playerRef}
                className={`live-monitor-player w-full h-full bg-black ${showError ? 'opacity-0' : ''}`}
                style={{
                  transform: zoomCss,
                  transformOrigin: 'center center',
                  willChange: digitalZoom.scale > 1 ? 'transform' : undefined,
                }}
                data-digital-zoom-scale={digitalZoom.scale}
              />
            </div>
          ) : (
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-500 bg-black">
              <div className="text-white">Camera Offline</div>
            </div>
          )}

          {/* Sibling of zoomed player — OSD not scaled by digital zoom (RDSO 18.2.29). */}
          <VideoTitleTimeOverlay
            camera={camera}
            size="md"
            showTitle
            position="top-left"
            className="mt-14 sm:mt-16 ml-2"
          />

          <div className="absolute top-0 left-0 right-0 p-4 flex justify-between items-start bg-gradient-to-b from-black/80 to-transparent gap-3">
            <div className="flex flex-col gap-2 min-w-0 flex-1">
              <div className="flex items-center space-x-2 min-w-0">
                {isRecording && (
                  <div className="flex-shrink-0 flex items-center bg-red-600 text-white text-xs font-bold pl-1.5 pr-2 py-0.5 rounded-full">
                    <span className="rec-dot mr-1"></span>
                    <span>REC</span>
                  </div>
                )}
                <h2 className="font-bold text-white text-xl truncate">{cameraTileLabel(camera)}</h2>
              </div>
              {alarmBanner?.event && (
                <div
                  className="rounded-lg border border-red-500/60 bg-red-950/85 px-3 py-2 text-left shadow-lg max-w-xl"
                  onDoubleClick={stopDoubleClick}
                  data-testid="alarm-display-banner"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-wide text-red-200">
                    <span>Alarm display</span>
                    <span className="rounded px-1.5 py-0.5 bg-red-600/80 text-white normal-case">
                      Alarmed
                    </span>
                    {(alarmBanner.queueCount || 0) > 1 && (
                      <span className="rounded px-1.5 py-0.5 bg-black/40 text-amber-200 normal-case">
                        +{(alarmBanner.queueCount || 1) - 1} more
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-sm text-white font-medium truncate">
                    {cameraTileLabel(camera)} · {sourceTypeLabel(alarmBanner.event.source_type)}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-200">
                    <span className={`rounded px-1.5 py-0.5 ${severityBadgeClass(alarmBanner.event.severity)}`}>
                      {alarmBanner.event.severity}
                    </span>
                    <span>{formatOccurredAt(alarmBanner.event.occurred_at)}</span>
                    <span className="text-gray-400 truncate max-w-[14rem]">{alarmBanner.event.title}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {alarmBanner.onManualReset && (
                      <button
                        type="button"
                        onClick={alarmBanner.onManualReset}
                        className="px-3 py-1.5 rounded bg-gray-200 text-gray-900 text-xs font-semibold hover:bg-white"
                      >
                        Reset display
                      </button>
                    )}
                    {alarmBanner.onAcknowledge && (
                      <button
                        type="button"
                        onClick={alarmBanner.onAcknowledge}
                        className="px-3 py-1.5 rounded bg-emerald-700 text-white text-xs font-semibold hover:bg-emerald-600"
                      >
                        Acknowledge
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center space-x-2 flex-wrap justify-end gap-y-1">
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

              <div
                className="bg-gray-700 border border-gray-600 text-white rounded-md px-2 py-1"
                onDoubleClick={stopDoubleClick}
              >
                <CameraSelector
                  cameras={allCameras}
                  selected={allCameras[currentIndex]}
                  onSelect={handleSelectCamera}
                />
              </div>
            </div>
          </div>

          <div className="absolute inset-x-0 bottom-0 p-4 bg-gradient-to-t from-black/80 to-transparent flex flex-col items-center gap-3">
            <div className="w-full flex flex-wrap items-end justify-between gap-3">
              {showLivePtz ? (
                <div className="self-start" onDoubleClick={stopDoubleClick} title="Optical PTZ (camera movement)">
                  <LivePtzPad cameraId={camera.id} online={camera.online} size="md" />
                </div>
              ) : (
                <div />
              )}
              {showDigitalZoom && (
                <DigitalZoomControls
                  zoom={digitalZoom}
                  onZoomIn={handleDigitalZoomIn}
                  onZoomOut={handleDigitalZoomOut}
                  onReset={handleDigitalZoomReset}
                  disabled={!camera.online}
                />
              )}
            </div>
            {!forceSub && streamStatus !== 'playing' && (
              <button
                type="button"
                onClick={handleUseLowQuality}
                onDoubleClick={stopDoubleClick}
                className="text-sm px-4 py-2 rounded-lg bg-amber-900/70 border border-amber-600/50 text-amber-100 hover:bg-amber-800/80 transition-colors"
              >
                Use sub stream (102)
              </button>
            )}
            {showManualRecordingControls && (
              <div className="flex items-center space-x-4" onDoubleClick={stopDoubleClick}>
                <button
                  onClick={() => onToggleRecording(camera.id)}
                  className={`flex items-center space-x-2 py-2 px-4 rounded transition-colors bg-black/30 backdrop-blur-sm ${
                    isRecording ? 'text-red-400' : 'text-gray-200 hover:bg-white/20'
                  }`}
                >
                  <Circle size={16} className={isRecording ? 'fill-current' : ''} />
                  <span>{isRecording ? 'Stop' : 'Record'}</span>
                </button>
              </div>
            )}
          </div>

          {isConnecting && !showError && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/40 pointer-events-none gap-2">
              <div className="flex items-center gap-2 text-white text-lg">
                <Loader2 className="animate-spin" size={22} />
                {statusLabel}
              </div>
            </div>
          )}

          {showError && (
            <div className="absolute inset-0 flex items-center justify-center bg-gray-900/95 px-6 z-20">
              <div className="text-center max-w-md space-y-5">
                <p className="text-white text-center text-sm max-w-xl leading-relaxed">{error}</p>
                <div className="flex flex-wrap justify-center gap-3">
                  <button
                    type="button"
                    onClick={handleRetry}
                    onDoubleClick={stopDoubleClick}
                    className="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-500"
                  >
                    Retry
                  </button>
                  {!forceSub && (
                    <button
                      type="button"
                      onClick={handleUseLowQuality}
                      onDoubleClick={stopDoubleClick}
                      className="px-4 py-2 rounded-lg bg-amber-700 text-white hover:bg-amber-600"
                    >
                      Use sub stream (102)
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={onClose}
                    onDoubleClick={stopDoubleClick}
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
            <div>Double-click video to return to grid • ◀ ▶ Arrow keys • ESC Close</div>
            <div className="text-gray-400 mt-1">
              Digital zoom: scroll wheel / + − · drag to pan when zoomed · 0 reset
              {camera.ptz ? ' · Optical PTZ pad is separate' : ''}
            </div>
            {streamName && <div className="text-gray-400 mt-1">Stream: {streamName}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
