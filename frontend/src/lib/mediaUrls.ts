import { fetchLiveConfig } from './liveProvider';

/** Normalize worker id from API (number or string). */
export function normalizeWorkerId(value: unknown): number | null {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.floor(n);
}

/**
 * WebSocket base path for go2rtc Live View playback.
 * Always Nginx → go2rtc worker: /media/w{id}/api/ws
 * Python /go2rtc/api/ws proxy was removed (Task 4B).
 */
export function go2rtcWsPath(workerId?: number | null): string {
  if (workerId == null || workerId <= 0) {
    throw new Error(
      'Direct media route unavailable — camera has no workerId (expected /media/w{N}/api/ws)',
    );
  }
  return `/media/w${workerId}/api/ws`;
}

/** Full video-stream `src` for a camera stream name. */
export function buildGo2RtcStreamSrc(stream: string, workerId?: number | null): string {
  const path = go2rtcWsPath(workerId);
  return `${path}?src=${encodeURIComponent(stream)}`;
}

/** Still JPEG from go2rtc (same Nginx /media auth as live WS). */
export function go2rtcFrameJpegSrc(
  stream: string,
  workerId?: number | string | null,
): string {
  const wid = normalizeWorkerId(workerId) ?? 1;
  return `/media/w${wid}/api/frame.jpeg?src=${encodeURIComponent(stream)}`;
}

/**
 * Optional readiness check — Live View always uses direct media.
 * Returns true when backend advertises media worker routes (Nginx expected).
 * Does not enable a Python fallback when false.
 */
export async function isDirectMediaReady(): Promise<boolean> {
  const config = await fetchLiveConfig();
  return Boolean(config.directMediaEnabled);
}
