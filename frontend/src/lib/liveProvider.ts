import { useEffect, useState } from 'react';

import { apiFetch } from './api';

export interface LiveConfig {
  go2rtcEnabled: boolean;
  go2rtcWorkersEnabled?: boolean;
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

/** Push MongoDB camera streams into go2rtc (fixes stale pilot config). */
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
