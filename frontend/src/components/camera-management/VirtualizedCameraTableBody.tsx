import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Camera as CameraIcon,
  Edit,
  Eye,
  Power,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react';
import StatusBadge from './StatusBadge';
import {
  MGMT_ROW_HEIGHT_PX,
  mgmtScrollTopForRowIndex,
  mgmtVisibleRowRange,
} from '../../lib/cameraManagementVirtual';

const ACTION_BTN =
  'inline-flex items-center gap-1 px-1.5 py-1 text-[11px] font-medium rounded hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-colors';

export interface ManagementTableCamera {
  id?: string;
  _id?: string;
  name: string;
  ip_address: string;
  displayName?: string;
  building?: string;
  floor?: string;
  location_path?: string;
  site?: string;
  camera_group?: string;
  is_active?: boolean;
  status?: string;
  online?: boolean;
  recordingActive?: boolean;
  lastError?: string | null;
  liveStatus?: string;
  confirmedOffline?: boolean;
}

function rowId(camera: ManagementTableCamera): string {
  return String(camera._id ?? camera.id ?? '');
}

function liveViewHref(camera: ManagementTableCamera): string {
  const q = new URLSearchParams();
  const id = rowId(camera);
  if (id) q.set('camera', id);
  if (camera.camera_group) q.set('group', camera.camera_group);
  if (camera.site) q.set('site', camera.site);
  const s = q.toString();
  return s ? `/live?${s}` : '/live';
}

interface VirtualizedCameraTableBodyProps {
  cameras: ManagementTableCamera[];
  highlightId: string | null;
  isAdmin: boolean;
  scrollContainerRef: React.RefObject<HTMLDivElement>;
  scopeKey: string;
  onEdit: (camera: ManagementTableCamera) => void;
  onOpenSnapshot: (camera: ManagementTableCamera) => void;
  onStreamProfile: (camera: ManagementTableCamera) => void;
  onToggleActive: (camera: ManagementTableCamera) => void;
  onDelete: (camera: ManagementTableCamera) => void;
}

export function scrollManagementTableToCamera(
  scrollContainer: HTMLDivElement | null,
  cameras: ManagementTableCamera[],
  cameraId: string,
): boolean {
  if (!scrollContainer || !cameraId) return false;
  const index = cameras.findIndex((c) => rowId(c) === cameraId);
  if (index < 0) return false;
  scrollContainer.scrollTop = mgmtScrollTopForRowIndex(index);
  return true;
}

export default function VirtualizedCameraTableBody({
  cameras,
  highlightId,
  isAdmin,
  scrollContainerRef,
  scopeKey,
  onEdit,
  onOpenSnapshot,
  onStreamProfile,
  onToggleActive,
  onDelete,
}: VirtualizedCameraTableBodyProps) {
  const rafScrollRef = useRef(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);

  const measureViewport = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const h = el.clientHeight;
    if (h > 0) setViewportHeight(h);
  }, [scrollContainerRef]);

  useLayoutEffect(() => {
    measureViewport();
    const el = scrollContainerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => measureViewport());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measureViewport, cameras.length, scopeKey, scrollContainerRef]);

  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
      rafScrollRef.current = requestAnimationFrame(() => {
        setScrollTop(el.scrollTop);
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      if (rafScrollRef.current) cancelAnimationFrame(rafScrollRef.current);
    };
  }, [scrollContainerRef, scopeKey]);

  useEffect(() => {
    setScrollTop(0);
    const el = scrollContainerRef.current;
    if (el) el.scrollTop = 0;
  }, [scopeKey, scrollContainerRef]);

  if (cameras.length === 0) {
    return (
      <tbody className="divide-y divide-gray-100 dark:divide-gray-700/80">
        <tr>
          <td colSpan={7} className="px-4 py-10 text-center text-gray-500">
            No cameras match the current filters.
          </td>
        </tr>
      </tbody>
    );
  }

  const { startIndex, endIndex, mountedCount, topPad, bottomPad } = mgmtVisibleRowRange(
    cameras.length,
    scrollTop,
    viewportHeight,
    MGMT_ROW_HEIGHT_PX,
  );

  const visibleCameras: ManagementTableCamera[] = [];
  if (endIndex >= startIndex) {
    for (let i = startIndex; i <= endIndex; i += 1) {
      const cam = cameras[i];
      if (cam) visibleCameras.push(cam);
    }
  }

  return (
    <tbody
      className="divide-y divide-gray-100 dark:divide-gray-700/80"
      data-mgmt-table-count={cameras.length}
      data-mgmt-table-mounted={mountedCount}
    >
      {topPad > 0 && (
        <tr aria-hidden className="border-0">
          <td colSpan={7} className="p-0 border-0" style={{ height: topPad }} />
        </tr>
      )}
      {visibleCameras.map((camera, offset) => {
        const index = startIndex + offset;
        const id = rowId(camera);
        const isDisabled = camera.status === 'Disabled' || camera.is_active === false;
        const hasError = Boolean(camera.lastError) && camera.liveStatus === 'offline';
        return (
          <tr
            key={id || `row-${index}`}
            id={`camera-row-${id}`}
            className={`hover:bg-gray-50 dark:hover:bg-gray-700/30 ${
              highlightId === id ? 'bg-amber-500/10' : ''
            }`}
            style={{ height: MGMT_ROW_HEIGHT_PX }}
          >
            <td className="px-3 py-1.5 align-middle">
              <div className="font-semibold text-gray-900 dark:text-white">{camera.name}</div>
              {camera.displayName && camera.displayName !== camera.name && (
                <div className="text-[10px] text-gray-500 truncate max-w-[10rem]">{camera.displayName}</div>
              )}
            </td>
            <td className="px-3 py-1.5 font-mono text-gray-600 dark:text-gray-300 align-middle">{camera.ip_address}</td>
            <td className="px-3 py-1.5 align-middle">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                {isDisabled ? (
                  <StatusBadge variant="disabled">Disabled</StatusBadge>
                ) : (
                  <StatusBadge variant={camera.online || camera.liveStatus === 'online' ? 'online' : 'offline'}>
                    {camera.online || camera.liveStatus === 'online' ? 'Online' : 'Offline'}
                  </StatusBadge>
                )}
                {hasError && camera.confirmedOffline !== false && camera.liveStatus === 'offline' && (
                  <StatusBadge variant="error">Error</StatusBadge>
                )}
              </div>
            </td>
            <td className="px-3 py-1.5 align-middle">
              {camera.recordingActive ? (
                <StatusBadge variant="recording">Recording</StatusBadge>
              ) : (
                <span className="text-gray-500">—</span>
              )}
            </td>
            <td
              className="px-3 py-1.5 text-gray-500 truncate max-w-[14rem] align-middle"
              title={camera.location_path || ''}
            >
              {camera.location_path || `${camera.building || ''} / ${camera.floor || ''}`}
            </td>
            <td
              className="px-3 py-1.5 text-red-400/90 truncate max-w-[12rem] align-middle"
              title={camera.lastError || ''}
            >
              {camera.lastError || '—'}
            </td>
            <td className="px-2 py-1.5 align-middle">
              <div className="flex flex-wrap justify-end gap-0.5">
                <Link to={liveViewHref(camera)} className={`${ACTION_BTN} text-sky-500`} title="View in Live View">
                  <Eye size={12} /> View
                </Link>
                {isAdmin && (
                  <>
                    <button
                      type="button"
                      onClick={() => onOpenSnapshot(camera)}
                      className={`${ACTION_BTN} text-violet-400`}
                      title="Snapshot"
                    >
                      <CameraIcon size={12} /> Snapshot
                    </button>
                    <button type="button" onClick={() => onEdit(camera)} className={`${ACTION_BTN} text-sky-400`}>
                      <Edit size={12} /> Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => onStreamProfile(camera)}
                      className={`${ACTION_BTN} text-teal-400`}
                      title="Stream profile (FPS / resolution)"
                    >
                      <SlidersHorizontal size={12} /> Stream
                    </button>
                    <button
                      type="button"
                      onClick={() => onToggleActive(camera)}
                      className={`${ACTION_BTN} ${isDisabled ? 'text-emerald-400' : 'text-amber-400'}`}
                    >
                      <Power size={12} />
                      {isDisabled ? 'Reactivate' : 'Disable'}
                    </button>
                    <button
                      type="button"
                      onClick={() => onDelete(camera)}
                      className={`${ACTION_BTN} text-red-400`}
                      title="Permanently delete this camera"
                    >
                      <Trash2 size={12} /> Delete
                    </button>
                  </>
                )}
              </div>
            </td>
          </tr>
        );
      })}
      {bottomPad > 0 && (
        <tr aria-hidden className="border-0">
          <td colSpan={7} className="p-0 border-0" style={{ height: bottomPad }} />
        </tr>
      )}
    </tbody>
  );
}
