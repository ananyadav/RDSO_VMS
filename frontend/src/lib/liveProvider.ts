import { useEffect, useState } from 'react';

export type LiveProvider = 'go2rtc' | 'hls';

export interface LiveConfig {
  provider: LiveProvider;
  hlsFallback: boolean;
  go2rtcEnabled: boolean;
}

const DEFAULT: LiveConfig = {
  provider: 'go2rtc',
  hlsFallback: true,
  go2rtcEnabled: true,
};

let cached: LiveConfig | null = null;
let pending: Promise<LiveConfig> | null = null;

export async function fetchLiveConfig(): Promise<LiveConfig> {
  if (cached) return cached;
  if (pending) return pending;
  pending = (async () => {
    try {
      const res = await fetch('/api/live/config', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        cached = {
          provider: data.provider === 'hls' ? 'hls' : 'go2rtc',
          hlsFallback: Boolean(data.hlsFallback),
          go2rtcEnabled: Boolean(data.go2rtcEnabled),
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

/** Push MongoDB camera streams into go2rtc (fixes stale pilot config). */
export function ensureGo2RtcStreamsSynced(): Promise<boolean> {
  if (syncPromise) return syncPromise;
  syncPromise = fetch('/api/go2rtc/sync', { method: 'POST' })
    .then((res) => res.ok)
    .catch(() => false);
  return syncPromise;
}

/** Force a fresh sync (e.g. after stream-not-found). */
export function resetGo2RtcStreamSync(): Promise<boolean> {
  syncPromise = null;
  return ensureGo2RtcStreamsSynced();
}

export function trackGo2RtcConsumer(stream: string, delta: 1 | -1): void {
  fetch('/api/go2rtc/consumer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream, delta }),
    keepalive: delta < 0,
  }).catch(() => {});
}
