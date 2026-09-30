/** Multi-camera archive playback helpers (RDSO 18.1.26 / 18.1.27 / 18.3.12.1). */

export const MAX_MULTI_PLAYBACK_CAMERAS = 16;

export const NO_RECORDING_AT_TIME = 'No recording available for this time.';

export interface MultiPlaybackRecording {
  sessionId: string;
  startTime: string;
  endTime: string;
  duration: number;
  playlistUrl: string;
  playable?: boolean;
  error?: string | null;
  segmentCount?: number;
}

export interface MultiPlaybackResolved {
  ok: boolean;
  sessionId?: string | null;
  playlistUrl?: string | null;
  offsetSeconds?: number | null;
  startTime?: string | null;
  endTime?: string | null;
  code?: string | null;
  error?: string | null;
}

export interface MultiPlaybackCameraResult {
  cameraId: string;
  cameraUid: string;
  cameraName: string;
  ok: boolean;
  recordings: MultiPlaybackRecording[];
  total: number;
  resolved: MultiPlaybackResolved | null;
}

export interface MultiPlaybackSearchResponse {
  date: string;
  timezone: string;
  at: string | null;
  cameras: MultiPlaybackCameraResult[];
  totalCameras: number;
  requestedCount?: number;
  authorizedCount?: number;
}

export function parseTimeOfDay(value: string): { hour: number; minute: number; second: number } | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!m) return null;
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  const second = m[3] != null ? Number(m[3]) : 0;
  if (
    !Number.isFinite(hour) ||
    !Number.isFinite(minute) ||
    !Number.isFinite(second) ||
    hour < 0 ||
    hour > 23 ||
    minute < 0 ||
    minute > 59 ||
    second < 0 ||
    second > 59
  ) {
    return null;
  }
  return { hour, minute, second };
}

export function resolveRecordingAtTime(
  recordings: MultiPlaybackRecording[],
  atMs: number,
): MultiPlaybackResolved {
  for (const rec of recordings) {
    if (rec.playable === false || rec.error) continue;
    const start = new Date(rec.startTime).getTime();
    const end = new Date(rec.endTime).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    if (atMs >= start && atMs <= end) {
      let offset = Math.max(0, (atMs - start) / 1000);
      if (rec.duration > 0) offset = Math.min(offset, rec.duration);
      return {
        ok: true,
        sessionId: rec.sessionId,
        playlistUrl: rec.playlistUrl,
        offsetSeconds: offset,
        startTime: rec.startTime,
        endTime: rec.endTime,
        code: null,
        error: null,
      };
    }
  }
  return {
    ok: false,
    sessionId: null,
    playlistUrl: null,
    offsetSeconds: null,
    startTime: null,
    endTime: null,
    code: 'no_footage',
    error: NO_RECORDING_AT_TIME,
  };
}

export function multiGridCols(cameraCount: number): number {
  if (cameraCount <= 1) return 1;
  if (cameraCount === 2) return 2;
  if (cameraCount <= 4) return 2;
  if (cameraCount <= 9) return 3;
  return 4; // 10–16 → up to 4×4
}

export function wallClockAfterDelta(atIso: string, deltaSeconds: number): string {
  const ms = new Date(atIso).getTime() + deltaSeconds * 1000;
  return new Date(ms).toISOString();
}

export function buildMultiSearchQuery(opts: {
  date: string;
  cameraRefs: string[];
  atIso?: string | null;
}): string {
  const params = new URLSearchParams();
  params.set('date', opts.date);
  for (const ref of opts.cameraRefs.slice(0, MAX_MULTI_PLAYBACK_CAMERAS)) {
    params.append('cameraUid', ref);
  }
  if (opts.atIso) params.set('at', opts.atIso);
  return params.toString();
}
