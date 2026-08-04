import { fetchLiveConfig } from './liveProvider';

/** Normalize worker id from API (number or string). */
export function normalizeWorkerId(value: unknown): number | null {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.floor(n);
}

/**
 * WebSocket base path for go2rtc playback.
 * When direct media is enabled (Nginx → worker), use /media/w{id}/api/ws.
 * Otherwise fall back to Python proxy /go2rtc/api/ws.
 */
export function go2rtcWsPath(workerId?: number | null, directMedia?: boolean): string {
  if (directMedia && workerId != null && workerId > 0) {
    return `/media/w${workerId}/api/ws`;
  }
  return '/go2rtc/api/ws';
}

/** Full video-stream `src` for a camera stream name. */
export function buildGo2RtcStreamSrc(
  stream: string,
  workerId?: number | null,
  directMedia?: boolean,
): string {
  const path = go2rtcWsPath(workerId, directMedia);
  return `${path}?src=${encodeURIComponent(stream)}`;
}

let directMediaCached: boolean | null = null;

export async function isDirectMediaEnabled(): Promise<boolean> {
  if (directMediaCached !== null) return directMediaCached;
  const config = await fetchLiveConfig();
  directMediaCached = Boolean(config.directMediaEnabled);
  return directMediaCached;
}
