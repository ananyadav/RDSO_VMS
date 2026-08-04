import { useEffect, useState } from 'react';

import { apiFetch } from './api';

export interface LiveConfig {
  go2rtcEnabled: boolean;
  go2rtcWorkersEnabled?: boolean;
  /** When true, WebSocket playback uses /media/w{workerId}/api/ws (Nginx direct). */
  directMediaEnabled?: boolean;
}

const DEFAULT: LiveConfig = {
  go2rtcEnabled: true,
};

let cached: LiveConfig | null = null;
let pending: Promise<LiveConfig> | null = null;

export async function fetchLiveConfig(): Promise<LiveConfig> {
  if (cached) return cached;
  if (pending) return pending;
  pending = (async () => {
    try {
      const res = await apiFetch('/api/go2rtc/live-config', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        cached = {
          go2rtcEnabled: Boolean(data.go2rtcEnabled),
          go2rtcWorkersEnabled: Boolean(data.go2rtcWorkersEnabled),
          directMediaEnabled: Boolean(data.directMediaEnabled),
        };
        return cached;
      }
    } catch {
      // use default
    }
    cached = DEFAULT;
    return cached;
  })();
  return pending;
}

export function useLiveConfig(): LiveConfig {
  const [config, setConfig] = useState<LiveConfig>(cached ?? DEFAULT);

  useEffect(() => {
    void fetchLiveConfig().then(setConfig);
  }, []);

  return config;
}

export function go2rtcStreamName(cameraUid: string, profile: 'sub' | 'main'): string {
  return `${cameraUid}_${profile}`;
}

let syncPromise: Promise<boolean> | null = null;

/** True when go2rtc API is already up (fast path — do not block Live View on full sync). */
export async function isGo2RtcRunning(): Promise<boolean> {
  try {
    const res = await apiFetch('/api/go2rtc/status', { cache: 'no-store' });
    if (!res.ok) return false;
    const data = await res.json();
    return Boolean(data.running);
  } catch {
    return false;
  }
}

/**
 * Wait until go2rtc (or worker fleet) is reachable.
 * Does not push MongoDB streams — full sync stays on startup / Diagnostics.
 */
export async function waitForGo2RtcReady(): Promise<boolean> {
  if (await isGo2RtcRunning()) return true;
  try {
    await apiFetch('/api/go2rtc/start', { method: 'POST' });
    for (let i = 0; i < 12; i += 1) {
      await new Promise((r) => setTimeout(r, 500));
      if (await isGo2RtcRunning()) return true;
    }
  } catch {
    // fall through
  }
  return isGo2RtcRunning();
}

/** Push MongoDB camera streams into go2rtc (admin / Diagnostics — not Live View mount). */
export function ensureGo2RtcStreamsSynced(): Promise<boolean> {
  if (syncPromise) return syncPromise;
  syncPromise = (async () => {
    try {
      const running = await isGo2RtcRunning();
      if (!running) {
        await apiFetch('/api/go2rtc/start', { method: 'POST' });
      }
      const res = await apiFetch('/api/go2rtc/sync', { method: 'POST' });
      return res.ok;
    } catch {
      return false;
    }
  })();
  return syncPromise;
}

/** Force a fresh sync (e.g. after stream-not-found). */
export function resetGo2RtcStreamSync(): Promise<boolean> {
  syncPromise = null;
  return ensureGo2RtcStreamsSynced();
}

export function trackGo2RtcConsumer(stream: string, delta: 1 | -1): void {
  apiFetch('/api/go2rtc/consumer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream, delta }),
    keepalive: delta < 0,
  }).catch(() => {});
}

/** Persist a final Live View playback failure so Camera Management can show lastError. */
export function reportGo2RtcClientError(payload: {
  cameraId: string;
  cameraUid?: string;
  stream?: string;
  message: string;
}): void {
  apiFetch('/api/go2rtc/client-error', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).catch(() => {});
}
