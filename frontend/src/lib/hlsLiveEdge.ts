import type Hls from 'hls.js';

/** Re-seek if playback falls more than this far behind live edge (periodic maintenance). */
export const LIVE_EDGE_MAINTAIN_DELAY_SEC = 4;

/** On startup, show UI and jump if further behind than this. */
export const LIVE_EDGE_START_JUMP_SEC = 6;

/** If buffered ahead exceeds this, jump to live edge. */
export const LIVE_EDGE_MAX_BUFFER_SEC = 8;

export function getLiveSyncPosition(hls: Hls): number | null {
  const edge = hls.liveSyncPosition;
  if (edge != null && Number.isFinite(edge)) {
    return edge;
  }
  return null;
}

export function getLiveDelaySec(hls: Hls, video: HTMLVideoElement): number | null {
  const edge = getLiveSyncPosition(hls);
  if (edge == null) return null;
  return Math.max(0, edge - video.currentTime);
}

export function getBufferLengthSec(video: HTMLVideoElement): number | null {
  const buffered = video.buffered;
  if (buffered.length === 0) return null;
  return Math.max(0, buffered.end(buffered.length - 1) - video.currentTime);
}

/** Seek to hls.js live sync position. Returns true if a seek was applied. */
export function seekToLiveEdge(hls: Hls, video: HTMLVideoElement): boolean {
  const edge = getLiveSyncPosition(hls);
  if (edge == null) return false;
  video.currentTime = edge;
  return true;
}

export function shouldJumpToLive(
  hls: Hls,
  video: HTMLVideoElement,
  delayThresholdSec: number,
): boolean {
  const delay = getLiveDelaySec(hls, video);
  if (delay != null && delay > delayThresholdSec) return true;
  const buffer = getBufferLengthSec(video);
  return buffer != null && buffer > LIVE_EDGE_MAX_BUFFER_SEC;
}
