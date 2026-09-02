import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { GripVertical, ListOrdered } from 'lucide-react';
import { cameraTileLabel } from '../lib/cameraLabel';
import {
  POOL_DEFAULT_ITEM_HEIGHT,
  POOL_GAP_PX,
  POOL_OVERSCAN_ITEMS,
  poolItemStride,
  poolTotalHeight,
  poolVisibleIndexRange,
} from '../lib/liveCameraPoolVirtual';
import {
  LIVE_CAMERA_DRAG_MIME,
  LIVE_CAMERA_SEQUENCE_DRAG_MIME,
} from '../lib/liveTileAssignments';
import type { CameraSequence } from '../lib/cameraSequencesApi';
import type { LiveGridCamera } from './LiveCameraGrid';

interface LiveCameraPoolProps {
  cameras: LiveGridCamera[];
  sequences?: CameraSequence[];
  assignedCameraIds: Set<string>;
  assignedSequenceIds?: Set<string>;
  hidden?: boolean;
  variant?: 'sidebar' | 'strip';
}

function CameraPoolItem({ camera }: { camera: LiveGridCamera }) {
  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData(LIVE_CAMERA_DRAG_MIME, camera.id);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="flex items-center gap-1.5 px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 cursor-grab active:cursor-grabbing hover:border-emerald-500/60 select-none"
      title="Drag to a tile — does not start a stream until dropped"
    >
      <GripVertical size={14} className="shrink-0 text-gray-400" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium truncate">{cameraTileLabel(camera)}</p>
        {camera.ip_address && (
          <p className="text-[10px] text-gray-500 font-mono truncate">{camera.ip_address}</p>
        )}
      </div>
      <span
        className={`shrink-0 w-1.5 h-1.5 rounded-full ${camera.online ? 'bg-emerald-500' : 'bg-gray-500'}`}
        aria-hidden
      />
    </div>
  );
}

function SequencePoolItem({ sequence }: { sequence: CameraSequence }) {
  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData(LIVE_CAMERA_SEQUENCE_DRAG_MIME, sequence.id);
    e.dataTransfer.effectAllowed = 'move';
  };

  return (
    <div
      draggable
      onDragStart={onDragStart}
      className="flex items-center gap-1.5 px-2 py-1.5 rounded border border-violet-400/40 bg-violet-950/20 cursor-grab active:cursor-grabbing hover:border-violet-400/80 select-none"
      title="Drag sequence to a tile for automatic rotation"
    >
      <ListOrdered size={14} className="shrink-0 text-violet-400" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium truncate text-violet-100">{sequence.name}</p>
        <p className="text-[10px] text-gray-500">
          {sequence.camera_ids.length} cam · {sequence.dwell_seconds}s dwell
        </p>
      </div>
    </div>
  );
}

export default function LiveCameraPool({
  cameras,
  sequences = [],
  assignedCameraIds,
  assignedSequenceIds = new Set(),
  hidden = false,
  variant = 'sidebar',
}: LiveCameraPoolProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const rafScrollRef = useRef(0);
  const scopeKeyRef = useRef('');

  const [viewportHeight, setViewportHeight] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);

  const itemHeightPx = POOL_DEFAULT_ITEM_HEIGHT;
  const scopeKey = `${cameras.length}:${cameras[0]?.id ?? ''}:${cameras[cameras.length - 1]?.id ?? ''}`;

  const measureViewport = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const h = viewport.clientHeight;
    if (h > 0) setViewportHeight(h);
  }, []);

  useLayoutEffect(() => {
    measureViewport();
    const viewport = viewportRef.current;
    if (!viewport) return;
    const ro = new ResizeObserver(() => measureViewport());
    ro.observe(viewport);
    return () => ro.disconnect();
  }, [measureViewport, cameras.length]);

  useEffect(() => {
    if (scopeKeyRef.current === scopeKey) return;
    scopeKeyRef.current = scopeKey;
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTop = 0;
      setScrollTop(0);
    }
  }, [scopeKey]);

  const onScroll = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
    rafScrollRef.current = requestAnimationFrame(() => setScrollTop(viewport.scrollTop));
  };

  useEffect(() => () => {
    if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
  }, []);

  if (hidden) return null;

  const playableSequences = sequences.filter(
    (seq) => seq.enabled && seq.camera_ids.length > 0 && !assignedSequenceIds.has(seq.id),
  );
  const unassignedCameras = cameras.filter((c) => !assignedCameraIds.has(c.id));

  if (variant === 'strip') {
    return (
      <aside className="shrink-0 border-t border-gray-300 dark:border-gray-700 bg-gray-100 dark:bg-gray-900/90 md:hidden">
        <div className="px-2 py-1.5 flex items-center justify-between gap-2 border-b border-gray-300 dark:border-gray-700">
          <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500">
            Cameras & sequences
          </span>
        </div>
        <div className="overflow-x-auto p-2 flex gap-2 scrollbar-hide">
          {unassignedCameras.map((camera) => (
            <div key={camera.id} className="shrink-0 w-[9.5rem]">
              <CameraPoolItem camera={camera} />
            </div>
          ))}
          {playableSequences.map((sequence) => (
            <div key={sequence.id} className="shrink-0 w-[9.5rem]">
              <SequencePoolItem sequence={sequence} />
            </div>
          ))}
        </div>
      </aside>
    );
  }

  const stride = poolItemStride(itemHeightPx, POOL_GAP_PX);
  const totalHeight = poolTotalHeight(cameras.length, itemHeightPx, POOL_GAP_PX);
  const { startIndex, endIndex, mountedCount } = poolVisibleIndexRange(
    cameras.length,
    scrollTop,
    viewportHeight,
    itemHeightPx,
    POOL_OVERSCAN_ITEMS,
    POOL_GAP_PX,
  );

  const mountedIndices: number[] = [];
  if (endIndex >= startIndex) {
    for (let i = startIndex; i <= endIndex; i += 1) mountedIndices.push(i);
  }

  return (
    <aside className="hidden md:flex w-52 shrink-0 flex-col border-r border-gray-300 dark:border-gray-700 bg-gray-100 dark:bg-gray-900/80 min-h-0">
      <div className="shrink-0 px-2.5 py-2 border-b border-gray-300 dark:border-gray-700">
        <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Cameras</span>
        <p className="text-[10px] text-gray-500 mt-0.5">Drag onto a tile</p>
      </div>
      <div
        ref={viewportRef}
        className="flex-1 min-h-0 overflow-y-auto p-2"
        onScroll={onScroll}
        data-live-pool-count={cameras.length}
        data-live-pool-mounted={mountedCount}
      >
        {cameras.length === 0 ? (
          <p className="text-xs text-gray-500 px-1">No cameras in this location.</p>
        ) : (
          <div className="relative w-full" style={{ height: totalHeight > 0 ? totalHeight : undefined }}>
            {mountedIndices.map((index) => {
              const camera = cameras[index];
              if (!camera) return null;
              return (
                <div
                  key={camera.id}
                  className={`absolute left-0 right-0 ${assignedCameraIds.has(camera.id) ? 'opacity-60' : ''}`}
                  style={{ top: index * stride, height: itemHeightPx }}
                >
                  <CameraPoolItem camera={camera} />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {playableSequences.length > 0 && (
        <div className="shrink-0 border-t border-gray-300 dark:border-gray-700 p-2 space-y-1.5 max-h-40 overflow-y-auto">
          <div className="px-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-violet-400/90">
              Sequences
            </span>
          </div>
          {playableSequences.map((sequence) => (
            <SequencePoolItem key={sequence.id} sequence={sequence} />
          ))}
        </div>
      )}
    </aside>
  );
}
