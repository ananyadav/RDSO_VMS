import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { Plus } from 'lucide-react';
import CameraCard from './CameraCard';
import SequenceTilePlayer from './SequenceTilePlayer';
import {
  LIVE_CAMERA_DRAG_MIME,
  LIVE_CAMERA_SEQUENCE_DRAG_MIME,
  type SlotAssignment,
  type SlotAssignments,
} from '../lib/liveTileAssignments';
import type { CameraSequence } from '../lib/cameraSequencesApi';

const GAP_PX = 2;
const OVERSCAN_ROWS = 1;

export interface LiveGridCamera {
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
  workerId?: number | string | null;
}

interface LiveCameraGridProps {
  slotAssignments: SlotAssignments;
  cameraById: Map<string, LiveGridCamera>;
  sequenceById: Map<string, CameraSequence>;
  gridCols: number;
  streamsReady: boolean;
  selectedCameraId?: string | null;
  fullscreenCameraId?: string | null;
  showFullscreenModal: boolean;
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
  onFullscreen?: (camera: LiveGridCamera) => void;
  onAssignCamera: (slotIndex: number, cameraId: string | null) => void;
  onAssignSequence: (slotIndex: number, sequenceId: string | null) => void;
  scrollResetKey: string | null;
  controlRoom?: boolean;
  dragDropEnabled?: boolean;
}

export interface LiveCameraGridHandle {
  getVisibleStartRow: () => number;
  restoreStartRow: (row: number) => void;
}

function readDragPayload(e: React.DragEvent): SlotAssignment | null {
  const sequenceId = e.dataTransfer.getData(LIVE_CAMERA_SEQUENCE_DRAG_MIME)?.trim();
  if (sequenceId) return { kind: 'sequence', id: sequenceId };
  const cameraId = e.dataTransfer.getData(LIVE_CAMERA_DRAG_MIME)?.trim();
  if (cameraId) return { kind: 'camera', id: cameraId };
  return null;
}

function acceptsDragTypes(types: readonly string[]): boolean {
  return (
    types.includes(LIVE_CAMERA_DRAG_MIME) || types.includes(LIVE_CAMERA_SEQUENCE_DRAG_MIME)
  );
}

const LiveCameraGrid = forwardRef<LiveCameraGridHandle, LiveCameraGridProps>(function LiveCameraGrid(
  {
    slotAssignments,
    cameraById,
    sequenceById,
    gridCols,
    streamsReady,
    selectedCameraId,
    fullscreenCameraId,
    showFullscreenModal,
    recordingSchedule,
    onToggleRecording,
    onFullscreen,
    onAssignCamera,
    onAssignSequence,
    scrollResetKey,
    controlRoom = false,
    dragDropEnabled = true,
  },
  ref,
) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [rowHeightPx, setRowHeightPx] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [dragOverSlot, setDragOverSlot] = useState<number | null>(null);
  const rafScrollRef = useRef(0);
  const rowHeightRef = useRef(0);

  const slotCount = slotAssignments.length;
  const totalRows = Math.ceil(slotCount / gridCols) || 0;
  const rowStride = rowHeightPx > 0 ? rowHeightPx + GAP_PX : 0;
  const totalHeight =
    totalRows > 0 && rowHeightPx > 0
      ? totalRows * rowHeightPx + Math.max(0, totalRows - 1) * GAP_PX
      : 0;

  const measure = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const h = el.clientHeight;
    const w = el.clientWidth;
    if (h <= 0) return;
    const minRow = gridCols >= 6 ? 48 : gridCols >= 5 ? 64 : 80;
    const visibleRows = Math.max(1, gridCols);
    const isPhone = w > 0 && w < 768;
    let nextRow: number;
    if (isPhone) {
      nextRow = Math.max(minRow, Math.ceil((h - GAP_PX * (visibleRows - 1)) / visibleRows));
      if (gridCols === 1) nextRow = Math.max(minRow, h);
    } else {
      nextRow = Math.max(minRow, Math.ceil((h - GAP_PX * (gridCols - 1)) / gridCols));
    }
    const prevRow = rowHeightRef.current;
    const prevStride = prevRow > 0 ? prevRow + GAP_PX : 0;
    const startRow = prevStride > 0 ? Math.max(0, Math.floor(el.scrollTop / prevStride)) : 0;
    setViewportHeight(h);
    if (nextRow === prevRow) return;
    rowHeightRef.current = nextRow;
    setRowHeightPx(nextRow);
    if (prevRow > 0) {
      const nextTop = startRow * (nextRow + GAP_PX);
      el.scrollTop = nextTop;
      setScrollTop(nextTop);
    }
  }, [gridCols]);

  useLayoutEffect(() => {
    measure();
    const el = viewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, slotCount, scrollResetKey]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    el.scrollTop = 0;
    setScrollTop(0);
  }, [scrollResetKey]);

  const onScroll = () => {
    const el = viewportRef.current;
    if (!el) return;
    if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
    rafScrollRef.current = requestAnimationFrame(() => setScrollTop(el.scrollTop));
  };

  useEffect(() => () => {
    if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      getVisibleStartRow: () => {
        const el = viewportRef.current;
        if (!el || rowStride <= 0) return 0;
        return Math.max(0, Math.floor(el.scrollTop / rowStride));
      },
      restoreStartRow: (row: number) => {
        const el = viewportRef.current;
        if (!el || rowStride <= 0) return;
        const maxRow = Math.max(0, totalRows - 1);
        el.scrollTop = Math.min(maxRow, Math.max(0, row)) * rowStride;
        setScrollTop(el.scrollTop);
      },
    }),
    [rowStride, totalRows],
  );

  let startRow = 0;
  let endRow = -1;
  if (rowStride > 0 && totalRows > 0) {
    startRow = Math.max(0, Math.floor(scrollTop / rowStride) - OVERSCAN_ROWS);
    endRow = Math.min(totalRows - 1, Math.ceil((scrollTop + viewportHeight) / rowStride) + OVERSCAN_ROWS);
  }

  let visibleStartRow = 0;
  let visibleEndRow = -1;
  if (rowStride > 0 && totalRows > 0 && viewportHeight > 0) {
    visibleStartRow = Math.max(0, Math.floor(scrollTop / rowStride));
    visibleEndRow = Math.min(
      totalRows - 1,
      Math.max(visibleStartRow, Math.ceil((scrollTop + viewportHeight) / rowStride) - 1),
    );
  }

  const mountedRows: number[] = [];
  for (let r = startRow; r <= endRow; r += 1) mountedRows.push(r);

  const applyDrop = (slotIndex: number, assignment: SlotAssignment | null) => {
    if (!assignment) return;
    if (assignment.kind === 'camera') {
      if (!cameraById.has(assignment.id)) return;
      onAssignCamera(slotIndex, assignment.id);
      return;
    }
    if (!sequenceById.has(assignment.id)) return;
    onAssignSequence(slotIndex, assignment.id);
  };

  const handleDrop = (slotIndex: number, e: React.DragEvent) => {
    e.preventDefault();
    setDragOverSlot(null);
    if (!dragDropEnabled) return;
    applyDrop(slotIndex, readDragPayload(e));
  };

  const handleDragOver = (slotIndex: number, e: React.DragEvent) => {
    if (!dragDropEnabled || !acceptsDragTypes(e.dataTransfer.types)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverSlot(slotIndex);
  };

  return (
    <div
      ref={viewportRef}
      className={`flex-1 min-h-0 overflow-y-auto bg-black ${controlRoom ? 'live-control-room-scroll' : ''}`}
      onScroll={onScroll}
      data-live-grid-cols={gridCols}
      data-live-grid-slots={slotCount}
    >
      <div className="relative w-full" style={{ height: totalHeight > 0 ? totalHeight : undefined }}>
        {mountedRows.map((row) => {
          const top = row * rowStride;
          const rowStart = row * gridCols;
          const rowStrictlyVisible = row >= visibleStartRow && row <= visibleEndRow;
          return (
            <div
              key={`row-${row}`}
              className="absolute left-0 right-0 grid gap-0.5"
              style={{
                top,
                height: rowHeightPx,
                gridTemplateColumns: `repeat(${gridCols}, minmax(0, 1fr))`,
              }}
            >
              {Array.from({ length: gridCols }, (_, col) => {
                const slotIndex = rowStart + col;
                if (slotIndex >= slotCount) {
                  return <div key={`empty-col-${col}`} className="min-w-0 h-full" />;
                }
                const assignment = slotAssignments[slotIndex];
                const isDropTarget = dragOverSlot === slotIndex;

                if (!assignment) {
                  return (
                    <div
                      key={`slot-${slotIndex}`}
                      className={`relative min-w-0 h-full border-2 border-dashed rounded-sm flex items-center justify-center ${
                        isDropTarget ? 'border-emerald-400 bg-emerald-950/30' : 'border-gray-700/80 bg-gray-950/50'
                      } ${dragDropEnabled && !controlRoom ? '' : 'pointer-events-none opacity-40'}`}
                      onDragOver={(e) => handleDragOver(slotIndex, e)}
                      onDragLeave={() => setDragOverSlot((s) => (s === slotIndex ? null : s))}
                      onDrop={(e) => handleDrop(slotIndex, e)}
                    >
                      {!controlRoom && (
                        <div className="flex flex-col items-center gap-1 text-gray-500 pointer-events-none">
                          <Plus size={18} />
                          <span className="text-[10px]">Drop camera or sequence</span>
                        </div>
                      )}
                    </div>
                  );
                }

                if (assignment.kind === 'sequence') {
                  const sequence = sequenceById.get(assignment.id);
                  return (
                    <div
                      key={`slot-${slotIndex}-seq-${assignment.id}`}
                      className={`relative min-w-0 h-full ${isDropTarget ? 'ring-2 ring-violet-400 z-20' : ''}`}
                      onDragOver={(e) => handleDragOver(slotIndex, e)}
                      onDragLeave={() => setDragOverSlot((s) => (s === slotIndex ? null : s))}
                      onDrop={(e) => handleDrop(slotIndex, e)}
                    >
                      {sequence ? (
                        <SequenceTilePlayer
                          sequence={sequence}
                          cameraById={cameraById}
                          eagerLive={rowStrictlyVisible}
                          observeRootRef={viewportRef}
                          streamsReady={streamsReady}
                          liveActive={!(showFullscreenModal && fullscreenCameraId != null)}
                          recordingSchedule={recordingSchedule}
                          onToggleRecording={onToggleRecording}
                          onFullscreen={onFullscreen}
                          controlRoom={controlRoom}
                        />
                      ) : (
                        <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-xs">
                          Sequence unavailable
                        </div>
                      )}
                    </div>
                  );
                }

                const camera = cameraById.get(assignment.id);
                const forceEager =
                  rowStrictlyVisible || (camera != null && camera.id === selectedCameraId);

                if (!camera) {
                  return (
                    <div
                      key={`slot-${slotIndex}`}
                      className="relative min-w-0 h-full border border-gray-700 bg-gray-950 flex items-center justify-center text-xs text-gray-500"
                      onDragOver={(e) => handleDragOver(slotIndex, e)}
                      onDrop={(e) => handleDrop(slotIndex, e)}
                    >
                      Camera unavailable
                    </div>
                  );
                }

                return (
                  <div
                    key={`slot-${slotIndex}-cam-${camera.id}`}
                    className={`relative min-w-0 h-full ${
                      !controlRoom && selectedCameraId === camera.id ? 'ring-2 ring-blue-400 z-10' : ''
                    } ${isDropTarget ? 'ring-2 ring-emerald-400 z-20' : ''}`}
                    onDragOver={(e) => handleDragOver(slotIndex, e)}
                    onDragLeave={() => setDragOverSlot((s) => (s === slotIndex ? null : s))}
                    onDrop={(e) => handleDrop(slotIndex, e)}
                    draggable={dragDropEnabled && !controlRoom}
                    onDragStart={(e) => {
                      if (!dragDropEnabled || controlRoom) return;
                      e.dataTransfer.setData(LIVE_CAMERA_DRAG_MIME, camera.id);
                      e.dataTransfer.effectAllowed = 'move';
                    }}
                  >
                    <div className="absolute inset-0">
                      <CameraCard
                        key={camera.id}
                        camera={camera}
                        eagerLive={forceEager}
                        observeRootRef={viewportRef}
                        streamsReady={streamsReady}
                        liveActive={!(showFullscreenModal && fullscreenCameraId === camera.id)}
                        isRecording={recordingSchedule[camera.id] || false}
                        onToggleRecording={() => onToggleRecording(camera.id)}
                        onFullscreen={onFullscreen}
                        controlRoom={controlRoom}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
});

export default React.memo(LiveCameraGrid);
