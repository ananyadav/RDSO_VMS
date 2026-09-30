import React, { useEffect, useRef, useState } from 'react';
import { Loader2, VideoOff } from 'lucide-react';
import { usePlaybackHLS } from '../../hooks/usePlaybackHLS';
import { NO_RECORDING_AT_TIME } from '../../lib/multiPlayback';

export interface MultiCameraPlaybackTileProps {
  cameraLabel: string;
  playlistUrl: string | null;
  initialSeek: number | null;
  noFootage: boolean;
  noFootageMessage?: string;
  masterPlaying: boolean;
  playbackSpeed: number;
  seekToken: number;
  seekTargetOffset: number | null;
  onReadyState?: (playing: boolean) => void;
}

/**
 * One synchronized archive player tile. Master clock drives play/pause/seek/speed;
 * missing footage is local to this tile.
 */
export default function MultiCameraPlaybackTile({
  cameraLabel,
  playlistUrl,
  initialSeek,
  noFootage,
  noFootageMessage = NO_RECORDING_AT_TIME,
  masterPlaying,
  playbackSpeed,
  seekToken,
  seekTargetOffset,
  onReadyState,
}: MultiCameraPlaybackTileProps): React.ReactElement {
  // Bind boot seek to playlist identity so shared-clock seeks do not remount HLS.
  const [bootSeek, setBootSeek] = useState<number | null>(initialSeek);
  const lastPlaylistRef = useRef<string | null>(null);
  useEffect(() => {
    if (playlistUrl !== lastPlaylistRef.current) {
      lastPlaylistRef.current = playlistUrl;
      setBootSeek(initialSeek);
    }
  }, [playlistUrl, initialSeek]);

  const {
    videoRef,
    loading,
    error,
    isPlaying,
    play,
    pause,
    seek,
    setPlaybackRate,
  } = usePlaybackHLS(noFootage ? null : playlistUrl, noFootage ? null : bootSeek);

  useEffect(() => {
    setPlaybackRate(playbackSpeed);
  }, [playbackSpeed, setPlaybackRate]);

  useEffect(() => {
    if (noFootage || !playlistUrl) return;
    if (masterPlaying) play();
    else pause();
  }, [masterPlaying, noFootage, playlistUrl, play, pause]);

  useEffect(() => {
    if (noFootage || !playlistUrl) return;
    if (seekTargetOffset == null || !Number.isFinite(seekTargetOffset)) return;
    seek(seekTargetOffset);
  }, [seekToken, seekTargetOffset, noFootage, playlistUrl, seek]);

  useEffect(() => {
    onReadyState?.(isPlaying);
  }, [isPlaying, onReadyState]);

  return (
    <div
      className="relative min-h-0 min-w-0 overflow-hidden bg-black ring-1 ring-gray-700"
      data-testid="multi-playback-tile"
      data-camera-label={cameraLabel}
      data-no-footage={noFootage ? 'true' : 'false'}
    >
      {!noFootage && playlistUrl ? (
        <video
          ref={videoRef}
          playsInline
          muted
          className="absolute inset-0 h-full w-full object-contain"
        />
      ) : null}

      {(loading || (!noFootage && !playlistUrl)) && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-gray-900/80">
          <Loader2 className="mb-2 animate-spin text-blue-400" size={24} />
          <p className="text-[10px] text-gray-400">Loading…</p>
        </div>
      )}

      {noFootage && (
        <div
          className="absolute inset-0 z-10 flex flex-col items-center justify-center bg-gray-950 px-3 text-center"
          data-testid="multi-playback-no-footage"
        >
          <VideoOff size={28} className="mb-2 opacity-40 text-amber-200" />
          <p className="text-xs text-amber-100">{noFootageMessage}</p>
        </div>
      )}

      {error && !noFootage && (
        <div className="absolute left-2 right-2 top-10 z-10 rounded bg-red-900/80 px-2 py-1 text-[10px] text-red-100">
          {error}
        </div>
      )}

      <div className="pointer-events-none absolute left-0 right-0 top-0 z-20 bg-gradient-to-b from-black/80 to-transparent p-1.5">
        <span className="truncate rounded bg-black/50 px-1.5 py-0.5 text-[10px] text-white">
          {cameraLabel}
        </span>
      </div>
    </div>
  );
}
