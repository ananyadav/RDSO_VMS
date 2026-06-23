import { apiFetch } from './api';

/**
 * Grid and fullscreen use independent stream ids and ref counts:
 *   Grid:       {cameraId}
 *   Fullscreen: {cameraId}__fullscreen
 *
 * Opening/closing fullscreen must never start or stop the grid stream.
 */

const FULLSCREEN_SUFFIX = '__fullscreen';

const refs = new Map<string, number>();
const startPromises = new Map<string, Promise<boolean>>();
const warmedFullscreen = new Set<string>();

export function streamKey(cameraId: string, fullscreen = false): string {
  return fullscreen ? `${cameraId}${FULLSCREEN_SUFFIX}` : cameraId;
}

export function livePlaylistUrl(cameraId: string, fullscreen = false): string {
  return `/api/live/${streamKey(cameraId, fullscreen)}/live.m3u8`;
}

export interface LiveStreamStatusResponse {
  streamId: string;
  active: boolean;
  ready: boolean;
  streamLabel: string | null;
  fallback: boolean;
  lastError: string | null;
  refCount: number;
}

export interface StartLiveOptions {
  forceSub?: boolean;
}

export async function fetchLiveStreamStatus(
  cameraId: string,
  fullscreen = false,
): Promise<LiveStreamStatusResponse | null> {
  try {
    const res = await apiFetch(`/api/live/${streamKey(cameraId, fullscreen)}/status`, {
      cache: 'no-store',
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function bustedUrl(url: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}_=${Date.now()}`;
}

export async function isPlaylistReady(
  cameraId: string,
  fullscreen = false,
  signal?: AbortSignal,
): Promise<boolean> {
  const url = livePlaylistUrl(cameraId, fullscreen);
  try {
    const res = await apiFetch(bustedUrl(url), { cache: 'no-store', signal });
    if (!res.ok) return false;
    const text = await res.text();
    return text.includes('#EXTM3U') && text.includes('.ts');
  } catch {
    return false;
  }
}

export async function waitForPlaylistReady(
  cameraId: string,
  fullscreen = false,
  maxMs = 20000,
  signal?: AbortSignal,
): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    if (signal?.aborted) return false;
    if (await isPlaylistReady(cameraId, fullscreen, signal)) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  return false;
}

async function doStart(streamId: string, options?: StartLiveOptions): Promise<boolean> {
  let pending = startPromises.get(streamId);
  if (pending) return pending;

  pending = (async () => {
    try {
      const res = await apiFetch(`/api/live/${streamId}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ forceSub: options?.forceSub ?? false }),
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      startPromises.delete(streamId);
    }
  })();

  startPromises.set(streamId, pending);
  return pending;
}

/**
 * Pre-start fullscreen FFmpeg (hover/click) without opening the modal yet.
 */
export async function warmFullscreenStream(cameraId: string): Promise<boolean> {
  const key = streamKey(cameraId, true);
  if ((refs.get(key) ?? 0) > 0 || warmedFullscreen.has(key)) {
    return true;
  }
  const ok = await doStart(key);
  if (ok) warmedFullscreen.add(key);
  return ok;
}

/**
 * Align frontend refs with batch-start so tiles do not POST /start again
 * (grid FFmpeg already running from batch).
 */
export function syncBatchGridRefs(
  results: Array<{ cameraId: string; status: string }>,
): void {
  for (const row of results) {
    if (row.status !== 'started' && row.status !== 'reused') continue;
    const key = streamKey(row.cameraId, false);
    if ((refs.get(key) ?? 0) === 0) {
      refs.set(key, 1);
    }
  }
}

/** Acquire live stream — grid tile or fullscreen viewer (separate stream ids). */
export async function acquireLiveStream(
  cameraId: string,
  fullscreen = false,
  options?: StartLiveOptions,
): Promise<boolean> {
  const key = streamKey(cameraId, fullscreen);
  warmedFullscreen.delete(key);
  const prev = refs.get(key) ?? 0;
  refs.set(key, prev + 1);
  if (prev === 0) {
    return doStart(key, options);
  }
  return true;
}

/** Restart fullscreen stream (e.g. manual sub/102 fallback). */
export async function restartFullscreenStream(
  cameraId: string,
  options: StartLiveOptions,
): Promise<boolean> {
  const key = streamKey(cameraId, true);
  refs.delete(key);
  warmedFullscreen.delete(key);
  try {
    await apiFetch(`/api/live/${key}/stop`, { method: 'POST' });
  } catch {
    // best effort
  }
  const ok = await doStart(key, options);
  if (ok) refs.set(key, 1);
  return ok;
}

/** Release only this stream id; grid and fullscreen are independent. */
export function releaseLiveStream(cameraId: string, fullscreen = false): void {
  const key = streamKey(cameraId, fullscreen);
  warmedFullscreen.delete(key);
  const prev = refs.get(key) ?? 0;
  if (prev <= 1) {
    refs.delete(key);
    apiFetch(`/api/live/${key}/stop`, { method: 'POST' }).catch(() => {});
  } else {
    refs.set(key, prev - 1);
  }
}

export interface BatchStartResult {
  results?: Array<{ cameraId: string; status: string }>;
}

/** Staggered grid batch start — does not touch fullscreen streams. */
export async function batchStartLiveStreams(cameraIds: string[]): Promise<void> {
  if (!cameraIds.length) return;
  try {
    const res = await apiFetch('/api/live/batch-start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cameraIds, profile: 'grid' }),
    });
    if (res.ok) {
      const data: BatchStartResult = await res.json();
      if (data.results?.length) {
        syncBatchGridRefs(data.results);
      }
    }
  } catch {
    // tiles acquire individually
  }
}
