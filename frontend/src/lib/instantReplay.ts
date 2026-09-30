/** Instant Replay API client + pure helpers (RDSO 18.1.11.6 / 18.2.8). */

import { apiFetch, cameraQuery } from './api';
import { hasRecordingView } from './permissions';
import type { User } from '../services/authService';

export const INSTANT_REPLAY_SPEEDS = [0.5, 1, 2, 4] as const;
export const INSTANT_REPLAY_PRESET_LABELS: Record<number, string> = {
  10: '10 sec',
  30: '30 sec',
  60: '1 min',
  300: '5 min',
};

export type InstantReplaySourceType = 'recording' | 'instant_replay_buffer' | null;

export interface InstantReplayConfig {
  enabled: boolean;
  bufferSeconds: number;
  segmentSeconds: number;
  idleGraceSeconds: number;
  presetsSeconds: number[];
}

export interface InstantReplayResolveResult {
  ok: boolean;
  cameraId?: string;
  cameraUid?: string;
  cameraName?: string;
  requestedAt?: string;
  oldestAvailableAt?: string | null;
  newestAvailableAt?: string | null;
  sourceType?: InstantReplaySourceType;
  sessionId?: string | null;
  playlistUrl?: string | null;
  offsetSeconds?: number | null;
  status?: string | null;
  error?: string;
  code?: string;
  config?: InstantReplayConfig;
}

export interface InstantReplayAvailability {
  ok: boolean;
  cameraId?: string;
  cameraUid?: string;
  cameraName?: string;
  oldestAvailableAt?: string | null;
  newestAvailableAt?: string | null;
  availableSeconds?: number;
  /** Practical short-buffer recent window (seconds). */
  recentAvailableSeconds?: number;
  presetsSeconds?: number[];
  hasActiveRecording?: boolean;
  bufferActive?: boolean;
  bufferSegmentCount?: number;
  permanentSegmentSeconds?: number;
  config?: InstantReplayConfig;
  now?: string;
  error?: string;
}

export interface InstantReplayLeaseResult {
  ok: boolean;
  leaseId?: string;
  cameraUid?: string;
  bufferStarted?: boolean;
  reason?: string;
  enabled?: boolean;
  error?: string;
  playlistUrl?: string;
  oldestAvailableAt?: string | null;
  newestAvailableAt?: string | null;
  segmentCount?: number;
  config?: InstantReplayConfig;
}

export function canUseInstantReplay(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
): boolean {
  return hasRecordingView(user);
}

/** Which go-back presets are enabled given available history seconds. */
export function enabledGoBackPresets(
  availableSeconds: number,
  presets: number[] = [10, 30, 60, 300],
): { seconds: number; label: string; enabled: boolean }[] {
  const avail = Math.max(0, availableSeconds);
  return presets.map((seconds) => ({
    seconds,
    label: INSTANT_REPLAY_PRESET_LABELS[seconds] ?? `${seconds}s`,
    enabled: seconds <= avail + 1,
  }));
}

/** Map video currentTime (seconds into playlist) to absolute ISO using window start. */
export function playheadIsoFromOffset(
  oldestAvailableAt: string | null | undefined,
  offsetSeconds: number,
): string | null {
  if (!oldestAvailableAt) return null;
  const start = Date.parse(oldestAvailableAt);
  if (!Number.isFinite(start)) return null;
  return new Date(start + Math.max(0, offsetSeconds) * 1000).toISOString();
}

/** Fraction 0..1 of playhead within [oldest, newest]. */
export function timelineFraction(
  oldestIso: string | null | undefined,
  newestIso: string | null | undefined,
  playheadIso: string | null | undefined,
): number {
  const oldest = oldestIso ? Date.parse(oldestIso) : NaN;
  const newest = newestIso ? Date.parse(newestIso) : NaN;
  const play = playheadIso ? Date.parse(playheadIso) : NaN;
  if (![oldest, newest, play].every(Number.isFinite) || newest <= oldest) return 0;
  return Math.max(0, Math.min(1, (play - oldest) / (newest - oldest)));
}

/** Click position on timeline → ISO timestamp clamped to available window. */
export function timelineClickToIso(
  oldestIso: string,
  newestIso: string,
  fraction: number,
): string {
  const oldest = Date.parse(oldestIso);
  const newest = Date.parse(newestIso);
  const f = Math.max(0, Math.min(1, fraction));
  const ms = oldest + f * Math.max(0, newest - oldest);
  return new Date(ms).toISOString();
}

/**
 * Recent lookbacks that permanent long-segment HLS cannot reliably serve
 * require IR buffer coverage (server uses the same rule).
 */
export function recentLookbackNeedsBuffer(
  secondsAgo: number,
  permanentSegmentSeconds = 300,
): boolean {
  return secondsAgo < permanentSegmentSeconds;
}

export function seekDeltaSeconds(direction: 'backward' | 'forward', step = 10): number {
  return direction === 'backward' ? -step : step;
}

export function cameraRefForApi(camera: {
  id: string;
  cameraUid?: string;
}): { cameraUid?: string; cameraId?: string } {
  if (camera.cameraUid) return { cameraUid: camera.cameraUid };
  return { cameraId: camera.id };
}

export async function fetchInstantReplayAvailability(
  camera: { id: string; cameraUid?: string },
): Promise<InstantReplayAvailability> {
  const ref = cameraRefForApi(camera);
  const res = await apiFetch(
    `/api/playback/instant-replay/availability${cameraQuery(ref)}`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return { ok: false, error: (err as { error?: string }).error || res.statusText };
  }
  return (await res.json()) as InstantReplayAvailability;
}

export async function resolveInstantReplay(opts: {
  camera: { id: string; cameraUid?: string };
  secondsAgo?: number;
  at?: string;
}): Promise<InstantReplayResolveResult> {
  const ref = cameraRefForApi(opts.camera);
  const params: Record<string, string | undefined> = { ...ref };
  if (opts.at) params.at = opts.at;
  else if (opts.secondsAgo != null) params.secondsAgo = String(opts.secondsAgo);
  const res = await apiFetch(
    `/api/playback/instant-replay/resolve${cameraQuery(params)}`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return {
      ok: false,
      error: (err as { error?: string }).error || res.statusText,
      code: 'http_error',
    };
  }
  return (await res.json()) as InstantReplayResolveResult;
}

export async function acquireInstantReplayLease(
  camera: { id: string; cameraUid?: string },
  leaseId?: string,
  opts?: { forceBuffer?: boolean },
): Promise<InstantReplayLeaseResult> {
  const ref = cameraRefForApi(camera);
  const res = await apiFetch('/api/playback/instant-replay/lease', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...ref,
      leaseId,
      forceBuffer: opts?.forceBuffer ?? false,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    return { ok: false, error: (err as { error?: string }).error || res.statusText };
  }
  return (await res.json()) as InstantReplayLeaseResult;
}

export async function heartbeatInstantReplayLease(
  camera: { id: string; cameraUid?: string },
  leaseId: string,
): Promise<InstantReplayLeaseResult> {
  const ref = cameraRefForApi(camera);
  const res = await apiFetch('/api/playback/instant-replay/lease/heartbeat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...ref, leaseId }),
  });
  if (!res.ok) {
    return { ok: false, error: 'heartbeat failed' };
  }
  return (await res.json()) as InstantReplayLeaseResult;
}

export async function releaseInstantReplayLease(
  camera: { id: string; cameraUid?: string },
  leaseId: string,
): Promise<void> {
  const ref = cameraRefForApi(camera);
  try {
    await apiFetch('/api/playback/instant-replay/lease/release', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...ref, leaseId }),
      keepalive: true,
    });
  } catch {
    // best-effort; TTL reaper cleans orphans
  }
}
