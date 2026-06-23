import React, { useRef } from 'react';
import { Loader2, VideoOff } from 'lucide-react';
import { useLiveStream } from '../hooks/useLiveStream';

export interface AccessCamera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
  location_path?: string;
  building?: string;
  floor?: string;
  camera_group?: string;
}

interface CameraAccessTileProps {
  camera: AccessCamera;
  checked: boolean;
  groupGranted: boolean;
  onToggle: (cameraUid: string) => void;
  active: boolean;
  streamSession: number;
}

export default function CameraAccessTile({
  camera,
  checked,
  groupGranted,
  onToggle,
  active,
  streamSession,
}: CameraAccessTileProps) {
  const tileRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const uid = camera.cameraUid || camera.id;

  const { provider, videoRef, playerContainerRef, isConnecting, error, streamStatus } = useLiveStream(
    {
      id: camera.id,
      name: camera.name,
      online: camera.online,
      ptz: false,
      activity: false,
      cameraUid: camera.cameraUid,
    },
    {
      observeRef: tileRef,
      playerContainerRef: playerRef,
      profile: 'sub',
      forceSub: true,
      active: active && camera.online,
      streamsReady: active,
      sessionKey: streamSession,
    },
  );

  const location =
    camera.location_path ||
    [camera.building, camera.floor].filter(Boolean).join(' / ') ||
    '—';

  return (
    <div
      ref={tileRef}
      className={`rounded-lg border overflow-hidden flex flex-col bg-gray-900/80 ${
        checked || groupGranted
          ? 'border-emerald-500/50 ring-1 ring-emerald-500/30'
          : 'border-gray-700'
      }`}
    >
      <div className="relative aspect-video bg-black">
        {camera.online && active ? (
          provider === 'go2rtc' ? (
            <div ref={playerRef} className="absolute inset-0 w-full h-full" />
          ) : (
            <video
              ref={videoRef}
              className="absolute inset-0 w-full h-full object-contain bg-black"
              muted
              autoPlay
              playsInline
            />
          )
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500">
            <VideoOff size={28} />
          </div>
        )}
        {isConnecting && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50">
            <Loader2 size={22} className="animate-spin text-gray-300" />
          </div>
        )}
        {error && (
          <div className="absolute bottom-0 left-0 right-0 bg-black/70 text-[10px] text-amber-300 px-2 py-1 truncate">
            {error}
          </div>
        )}
        <label
          className={`absolute top-2 left-2 flex items-center gap-1.5 rounded px-2 py-1 cursor-pointer z-10 ${
            groupGranted ? 'bg-emerald-600/90' : 'bg-black/70'
          }`}
        >
          <input
            type="checkbox"
            checked={checked || groupGranted}
            readOnly={groupGranted}
            onChange={() => {
              if (!groupGranted) onToggle(uid);
            }}
            className="access-checkbox"
          />
          {groupGranted && (
            <span className="text-[10px] font-semibold text-white">Floor</span>
          )}
        </label>
        {streamStatus === 'playing' && (
          <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        )}
      </div>
      <div className="p-2 space-y-0.5 text-xs min-h-[4.5rem]">
        <div className="font-semibold text-gray-100 truncate" title={camera.name}>
          {camera.name}
        </div>
        {camera.displayName && camera.displayName !== camera.name && (
          <div className="text-gray-400 truncate" title={camera.displayName}>
            {camera.displayName}
          </div>
        )}
        {camera.ip_address && (
          <div className="font-mono text-gray-500 truncate">{camera.ip_address}</div>
        )}
        <div className="text-gray-500 truncate" title={location}>
          {location}
        </div>
      </div>
    </div>
  );
}
