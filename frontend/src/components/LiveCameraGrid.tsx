import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import CameraCard from './CameraCard';

const GAP_PX = 2; // matches Tailwind gap-0.5
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
}

interface LiveCameraGridProps {
  cameras: LiveGridCamera[];
  gridCols: number;
  streamsReady: boolean;
  selectedCameraId?: string | null;
  fullscreenCameraId?: string | null;
  showFullscreenModal: boolean;
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
  onFullscreen: (camera: LiveGridCamera) => void;
  /** Reset scroll when location scope changes. */
  scrollResetKey: string | null;
}

/**
 * Row-based virtualized NxN live grid.
 * Full scroll height covers every camera; only nearby rows mount CameraCards.
 */
function LiveCameraGrid({
  cameras,
  gridCols,
  streamsReady,
  selectedCameraId,
  fullscreenCameraId,
  showFullscreenModal,
  recordingSchedule,
  onToggleRecording,
  onFullscreen,
  scrollResetKey,
}: LiveCameraGridProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [rowHeightPx, setRowHeightPx] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const rafScrollRef = useRef(0);

  const totalRows = Math.ceil(cameras.length / gridCols) || 0;
  const rowStride = rowHeightPx > 0 ? rowHeightPx + GAP_PX : 0;
  const totalHeight =
    totalRows > 0 && rowHeightPx > 0
      ? totalRows * rowHeightPx + Math.max(0, totalRows - 1) * GAP_PX
      : 0;

  const measure = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const h = el.clientHeight;
    const minRow = gridCols >= 5 ? 64 : 80;
    const nextRow =
      h > 0 ? Math.max(minRow, Math.floor((h - GAP_PX * (gridCols - 1)) / gridCols)) : 0;
    setViewportHeight(h);
    setRowHeightPx(nextRow);
  }, [gridCols]);

  useLayoutEffect(() => {
    measure();
    const el = viewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, cameras.length, scrollResetKey]);

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
    rafScrollRef.current = requestAnimationFrame(() => {
      setScrollTop(el.scrollTop);
    });
  };

  useEffect(() => {
    return () => {
      if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
    };
  }, []);

  let startRow = 0;
  let endRow = -1;
  if (rowStride > 0 && totalRows > 0) {
    startRow = Math.max(0, Math.floor(scrollTop / rowStride) - OVERSCAN_ROWS);
    endRow = Math.min(
      totalRows - 1,
      Math.ceil((scrollTop + viewportHeight) / rowStride) + OVERSCAN_ROWS,
    );
  }

  // Strictly visible rows only (no overscan) — these may open go2rtc immediately.
  let visibleStartRow = 0;
  let visibleEndRow = -1;
  if (rowStride > 0 && totalRows > 0 && viewportHeight > 0) {
    visibleStartRow = Math.max(0, Math.floor(scrollTop / rowStride));
    visibleEndRow = Math.min(
      totalRows - 1,
      Math.max(
        visibleStartRow,
        Math.ceil((scrollTop + viewportHeight) / rowStride) - 1,
      ),
    );
  }

  const mountedRows: number[] = [];
  for (let r = startRow; r <= endRow; r += 1) mountedRows.push(r);

  // Dev-facing count for Task 2 verification (also useful in React DevTools).
  const mountedCardCount = mountedRows.reduce((n, row) => {
    const start = row * gridCols;
    return n + Math.min(gridCols, Math.max(0, cameras.length - start));
  }, 0);

  let streamEligibleCount = 0;
  if (visibleEndRow >= visibleStartRow) {
    for (let r = visibleStartRow; r <= visibleEndRow; r += 1) {
      const start = r * gridCols;
      streamEligibleCount += Math.min(gridCols, Math.max(0, cameras.length - start));
    }
  }

  return (
    <div
      ref={viewportRef}
      className="flex-1 min-h-0 overflow-y-auto bg-black"
      onScroll={onScroll}
      data-live-grid-cols={gridCols}
      data-live-grid-total={cameras.length}
      data-live-grid-mounted={mountedCardCount}
      data-live-grid-stream-eligible={streamEligibleCount}
    >
      <div className="relative w-full" style={{ height: totalHeight > 0 ? totalHeight : undefined }}>
        {mountedRows.map((row) => {
          const top = row * rowStride;
          const startIndex = row * gridCols;
          const rowCams = cameras.slice(startIndex, startIndex + gridCols);
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
              {rowCams.map((camera) => {
                const forceEager =
                  rowStrictlyVisible || camera.id === selectedCameraId;
                return (
                  <div
                    key={camera.id}
                    className={`relative min-w-0 h-full ${
                      selectedCameraId === camera.id
                        ? 'ring-2 ring-blue-400 dark:ring-blue-500 z-10'
                        : ''
                    }`}
                  >
                    <div className="absolute inset-0">
                      <CameraCard
                        camera={camera}
                        // Mounted (incl. overscan) ≠ stream-eligible.
                        // Only strictly visible / selected tiles connect eagerly.
                        eagerLive={forceEager}
                        observeRootRef={viewportRef}
                        streamsReady={streamsReady}
                        liveActive={
                          !(showFullscreenModal && fullscreenCameraId === camera.id)
                        }
                        isRecording={recordingSchedule[camera.id] || false}
                        onToggleRecording={() => onToggleRecording(camera.id)}
                        onFullscreen={onFullscreen}
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
}

export default React.memo(LiveCameraGrid);
