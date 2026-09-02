import React, { useEffect, useMemo, useState } from 'react';
import { ListOrdered } from 'lucide-react';
import CameraCard from './CameraCard';
import type { LiveGridCamera } from './LiveCameraGrid';
import type { CameraSequence } from '../lib/cameraSequencesApi';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  advanceSequenceIndex,
  sequenceCameraOrder,
  sequencePositionLabel,
  shouldRotateSequence,
  dwellMsFromSeconds,
} from '../lib/sequencePlayback';

interface SequenceTilePlayerProps {
  sequence: CameraSequence;
  cameraById: Map<string, LiveGridCamera>;
  recordingSchedule?: Record<string, boolean>;
  eagerLive?: boolean;
  streamsReady?: boolean;
  observeRootRef?: React.RefObject<HTMLElement | null>;
  liveActive?: boolean;
  controlRoom?: boolean;
  onToggleRecording: (cameraId: string) => void;
  onFullscreen?: (camera: LiveGridCamera) => void;
}

export default function SequenceTilePlayer({
  sequence,
  cameraById,
  eagerLive = false,
  streamsReady = true,
  observeRootRef,
  liveActive = true,
  controlRoom = false,
  recordingSchedule = {},
  onToggleRecording,
  onFullscreen,
}: SequenceTilePlayerProps): React.ReactElement {
  const orderedIds = useMemo(
    () => sequenceCameraOrder(sequence.camera_ids),
    [sequence.camera_ids],
  );

  const [currentIndex, setCurrentIndex] = useState(0);

  const sequenceKey = `${sequence.id}:${orderedIds.join(',')}:${sequence.dwell_seconds}`;

  useEffect(() => {
    setCurrentIndex(0);
  }, [sequenceKey]);

  const total = orderedIds.length;
  const safeIndex = total > 0 ? Math.min(currentIndex, total - 1) : 0;
  const currentCameraId = total > 0 ? orderedIds[safeIndex] : null;
  const currentCamera = currentCameraId ? cameraById.get(currentCameraId) : undefined;

  useEffect(() => {
    if (!liveActive || !eagerLive || !shouldRotateSequence(total)) return undefined;
    const dwellMs = dwellMsFromSeconds(sequence.dwell_seconds);
    const timer = window.setTimeout(() => {
      setCurrentIndex((idx) => advanceSequenceIndex(idx, total));
    }, dwellMs);
    return () => window.clearTimeout(timer);
  }, [
    liveActive,
    eagerLive,
    total,
    sequence.dwell_seconds,
    safeIndex,
    currentCameraId,
    sequenceKey,
  ]);

  if (total === 0) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-950 text-gray-500 p-2 text-center">
        <ListOrdered size={32} className="mb-2 opacity-60" />
        <p className="text-xs font-medium">{sequence.name}</p>
        <p className="text-[10px] mt-1">No authorized cameras in this sequence</p>
      </div>
    );
  }

  if (!currentCamera) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-950 text-gray-500 p-2 text-center">
        <p className="text-xs font-medium">{sequence.name}</p>
        <p className="text-[10px] mt-1">Camera unavailable</p>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex flex-col min-h-0">
      {!controlRoom && (
        <div className="absolute top-0 left-0 right-0 z-20 px-1.5 py-1 flex items-start justify-between gap-1 bg-gradient-to-b from-black/75 to-transparent pointer-events-none">
          <div className="min-w-0">
            <div className="flex items-center gap-1 text-[10px] font-semibold text-violet-200 uppercase tracking-wide">
              <ListOrdered size={12} aria-hidden />
              <span className="truncate">{sequence.name}</span>
            </div>
            <p className="text-[10px] text-gray-200 truncate">
              {sequencePositionLabel(safeIndex, total)}
              {' · '}
              {cameraTileLabel(currentCamera)}
            </p>
            {total === 1 && (
              <p className="text-[9px] text-amber-300/90">Single authorized camera</p>
            )}
          </div>
        </div>
      )}
      <div className="flex-1 min-h-0 relative">
        <CameraCard
          key={`${sequence.id}-${currentCamera.id}-${safeIndex}`}
          camera={currentCamera}
          eagerLive={eagerLive}
          observeRootRef={observeRootRef}
          streamsReady={streamsReady}
          liveActive={liveActive}
          isRecording={Boolean(currentCamera && recordingSchedule[currentCamera.id])}
          onToggleRecording={onToggleRecording}
          onFullscreen={onFullscreen}
          controlRoom={controlRoom}
        />
      </div>
    </div>
  );
}
