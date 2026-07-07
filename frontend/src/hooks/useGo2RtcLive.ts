import { useEffect, useRef, useState, type RefObject } from 'react';
import { go2rtcStreamName, resetGo2RtcStreamSync } from '../lib/liveProvider';
import { acquireGo2RtcSlot, releaseGo2RtcSlot } from '../lib/go2rtcConnectionLimiter';
import { registerUiConsumer, unregisterUiConsumer } from '../lib/go2rtcConsumerRegistry';
import { mountGo2RtcPlayer } from '../lib/go2rtcPlayer';
import { cameraTileLabel } from '../lib/cameraLabel';

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
}

export type Go2RtcStreamStatus = 'idle' | 'connecting' | 'playing' | 'error';

const CONNECT_TIMEOUT_MS = 22000;
const MAX_RETRIES = 3;

interface UseGo2RtcLiveOptions {
  containerRef: RefObject<HTMLElement | null>;
  observeRef?: RefObject<HTMLElement | null>;
  profile: 'sub' | 'main';
  active?: boolean;
  eager?: boolean;
  sessionKey?: number;
  streamsReady?: boolean;
}

function cameraStreamLabel(camera: Camera): string {
  return cameraTileLabel(camera);
}

function friendlyError(camera: Camera, _stream: string, raw: string): string {
  const label = cameraStreamLabel(camera);
  const lower = raw.toLowerCase();
  if (lower.includes('not found')) {
    return `${label}: stream not registered in go2rtc — use Diagnostics → Reload`;
  }
  if (lower.includes('timed out') || lower.includes('timeout')) {
    return `${label}: connection timed out — check camera is online, on the same network, and RTSP credentials are correct`;
  }
  if (lower.includes('401') || lower.includes('auth') || lower.includes('password')) {
    return `${label}: wrong username or password for RTSP`;
  }
  if (lower.includes('refused') || lower.includes('unreachable')) {
    return `${label}: camera unreachable at RTSP port (network or firewall)`;
  }
  return raw.includes(label) ? raw : `${label}: ${raw}`;
}

async function waitForFirstFrame(
  container: HTMLElement,
  stream: string,
  mode: 'webrtc' | 'mse',
  timeoutMs: number,
): Promise<() => void> {
  return new Promise((resolve, reject) => {
    let cleanup: (() => void) | null = null;
    let frameSeen = false;

    const timeoutId = setTimeout(() => {
      cleanup?.();
      reject(new Error('Connection timed out'));
    }, timeoutMs);

    const finish = () => {
      clearTimeout(timeoutId);
      if (cleanup) resolve(cleanup);
    };

    void mountGo2RtcPlayer(container, {
      stream,
      mode,
      onFirstFrame: () => {
        frameSeen = true;
        finish();
      },
      onError: (msg: string) => {
        clearTimeout(timeoutId);
        cleanup?.();
        reject(new Error(msg || 'go2rtc playback error'));
      },
    })
      .then((fn) => {
        cleanup = fn;
        if (frameSeen) finish();
      })
      .catch((err: unknown) => {
        clearTimeout(timeoutId);
        reject(err instanceof Error ? err : new Error('Failed to load go2rtc player'));
      });
  });
}

export function useGo2RtcLive(camera: Camera | null, options: UseGo2RtcLiveOptions) {
  const containerRef = options.containerRef;
  const observeRef = options.observeRef ?? containerRef;
  const profile = options.profile;
  const active = options.active !== false;
  const eager = options.eager === true;
  const sessionKey = options.sessionKey ?? 0;
  const streamsReady = options.streamsReady !== false;

  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<Go2RtcStreamStatus>('idle');
  const [inView, setInView] = useState(eager);
  const [retryKey, setRetryKey] = useState(0);

  const sessionRef = useRef(0);
  const teardownRef = useRef<(() => void) | null>(null);
  const trackedStreamRef = useRef<string | null>(null);
  const slotHeldRef = useRef(false);

  const stopPlayer = (unregister = true) => {
    const stream = trackedStreamRef.current;
    teardownRef.current?.();
    teardownRef.current = null;
    if (unregister && stream) {
      unregisterUiConsumer(stream);
      trackedStreamRef.current = null;
    }
    if (slotHeldRef.current) {
      releaseGo2RtcSlot();
      slotHeldRef.current = false;
    }
  };

  useEffect(() => {
    if (eager) {
      setInView(true);
      return;
    }
    const el = observeRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting),
      { root: null, rootMargin: '150px', threshold: 0.05 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [camera?.id, eager, observeRef]);

  useEffect(() => {
    const shouldConnect = Boolean(camera?.online && active && streamsReady && inView);

    if (!shouldConnect) {
      stopPlayer(true);
      setIsConnecting(false);
      setError(null);
      setStreamStatus('idle');
      return;
    }

    const container = containerRef.current;
    if (!container || !camera) return;

    const stream = go2rtcStreamName(camera.cameraUid || camera.id, profile);
    const session = ++sessionRef.current;
    let cancelled = false;
    const modes: Array<'webrtc' | 'mse'> = ['mse'];

    const run = async () => {
      stopPlayer(true);
      setIsConnecting(true);
      setError(null);
      setStreamStatus('connecting');

      await acquireGo2RtcSlot();
      if (cancelled || session !== sessionRef.current) {
        releaseGo2RtcSlot();
        return;
      }
      slotHeldRef.current = true;

      for (let attempt = 0; attempt < MAX_RETRIES; attempt += 1) {
        if (cancelled || session !== sessionRef.current) return;

        const mode = modes[Math.min(attempt, modes.length - 1)];

        try {
          const cleanup = await waitForFirstFrame(container, stream, mode, CONNECT_TIMEOUT_MS);
          if (cancelled || session !== sessionRef.current) {
            cleanup();
            return;
          }

          registerUiConsumer(stream);
          trackedStreamRef.current = stream;
          teardownRef.current = cleanup;
          setIsConnecting(false);
          setStreamStatus('playing');
          setError(null);
          return;
        } catch (err) {
          stopPlayer(true);
          if (cancelled || session !== sessionRef.current) return;

          const raw = err instanceof Error ? err.message : 'Failed to connect';
          const message = friendlyError(camera, stream, raw);
          const isNotFound = raw.toLowerCase().includes('not found');
          const isLast = attempt >= MAX_RETRIES - 1;

          if (isNotFound && attempt === 0) {
            const ok = await resetGo2RtcStreamSync();
            await new Promise((r) => setTimeout(r, ok ? 500 : 1200));
            await acquireGo2RtcSlot();
            if (cancelled || session !== sessionRef.current) {
              releaseGo2RtcSlot();
              return;
            }
            slotHeldRef.current = true;
            continue;
          }

          if (!isLast) {
            setError(`${message} — retrying (${attempt + 2}/${MAX_RETRIES})…`);
            await new Promise((r) => setTimeout(r, 600 * (attempt + 1)));
            await acquireGo2RtcSlot();
            if (cancelled || session !== sessionRef.current) {
              releaseGo2RtcSlot();
              return;
            }
            slotHeldRef.current = true;
            continue;
          }

          setError(message);
          setIsConnecting(false);
          setStreamStatus('error');
          return;
        }
      }
    };

    void run();

    return () => {
      cancelled = true;
      if (session === sessionRef.current) {
        sessionRef.current += 1;
      }
      stopPlayer(true);
      setIsConnecting(false);
      setStreamStatus('idle');
    };
  }, [
    camera?.id,
    camera?.cameraUid,
    camera?.name,
    camera?.displayName,
    camera?.online,
    profile,
    active,
    inView,
    containerRef,
    sessionKey,
    streamsReady,
    retryKey,
  ]);

  useEffect(() => {
    const onPageHide = () => stopPlayer(true);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('pagehide', onPageHide);
      stopPlayer(true);
    };
  }, []);

  return {
    isConnecting,
    error,
    streamStatus,
    inView: eager || inView,
    streamName: camera ? go2rtcStreamName(camera.cameraUid || camera.id, profile) : null,
    retry: () => setRetryKey((k) => k + 1),
  };
}
