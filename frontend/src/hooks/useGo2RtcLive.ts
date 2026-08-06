import { useEffect, useRef, useState, type RefObject } from 'react';
import { go2rtcStreamName, reportGo2RtcClientError, reportGo2RtcClientOk } from '../lib/liveProvider';
import { acquireGo2RtcSlot, releaseGo2RtcSlot } from '../lib/go2rtcConnectionLimiter';
import { registerUiConsumer, unregisterUiConsumer } from '../lib/go2rtcConsumerRegistry';
import { mountGo2RtcPlayer } from '../lib/go2rtcPlayer';

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  ip_address?: string;
  online: boolean;
  workerId?: number | string | null;
}

export type Go2RtcStreamStatus = 'idle' | 'connecting' | 'playing' | 'error';

/** First attempt — cameras often need a few seconds for RTSP SETUP. */
const CONNECT_TIMEOUT_MS = 16000;
/** Later retries while tile keeps showing Connecting… */
const RETRY_TIMEOUT_MS = 10000;

interface UseGo2RtcLiveOptions {
  containerRef: RefObject<HTMLElement | null>;
  observeRef?: RefObject<HTMLElement | null>;
  profile: 'sub' | 'main';
  active?: boolean;
  eager?: boolean;
  sessionKey?: number;
  streamsReady?: boolean;
}

function isAuthError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes('401') ||
    lower.includes('auth') ||
    lower.includes('password') ||
    lower.includes('wrong user') ||
    // go2rtc often truncates to "streams: wrong"
    /streams:\s*wrong\b/.test(lower)
  );
}

function isSetupError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes('eof') ||
    lower.includes('setup') ||
    lower.includes('describe') ||
    lower.includes('rtsp')
  );
}

function isPermissionError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes('403') ||
    lower.includes('forbidden') ||
    lower.includes('access denied') ||
    lower.includes('permission')
  );
}

function isDisabledCameraError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return lower.includes('camera disabled') || /\bdisabled\b/.test(lower);
}

function isCodecError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes('codec') ||
    lower.includes('unsupported') ||
    lower.includes('h265') ||
    lower.includes('hevc')
  );
}

function isTemporaryError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes('timed out') ||
    lower.includes('timeout') ||
    lower.includes('econnreset') ||
    lower.includes('connection reset') ||
    lower.includes('502') ||
    lower.includes('503') ||
    lower.includes('bad gateway') ||
    lower.includes('service unavailable') ||
    lower.includes('cannot reach go2rtc') ||
    (lower.includes('worker') && lower.includes('unavailable'))
  );
}

/** True missing go2rtc stream name — not ONVIF/HTTP 404 or camera SETUP errors. */
function isGo2RtcStreamMissing(raw: string): boolean {
  const lower = raw.toLowerCase();
  if (/\bstream not found\b/.test(lower) || /\bstreams?:\s*not found\b/.test(lower)) {
    return true;
  }
  // Bare "not found" without camera/ONVIF/SETUP noise.
  return (
    lower.includes('not found') &&
    !lower.includes('onvif') &&
    !lower.includes('setup') &&
    !lower.includes('404') &&
    !lower.includes('453') &&
    !lower.includes('bandwidth')
  );
}

/** Errors that will not heal with a short wait — report once, still keep Connecting… */
function isNonRetryableError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    isAuthError(raw) ||
    isPermissionError(raw) ||
    isDisabledCameraError(raw) ||
    isCodecError(raw) ||
    isGo2RtcStreamMissing(raw) ||
    isSetupError(raw) ||
    lower.includes('453') ||
    lower.includes('not enough bandwidth') ||
    lower.includes('session expired')
  );
}

async function waitForFirstFrame(
  container: HTMLElement,
  stream: string,
  mode: 'webrtc' | 'mse',
  timeoutMs: number,
  workerId?: number | string | null,
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
      workerId,
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
  const [isQueued, setIsQueued] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<Go2RtcStreamStatus>('idle');
  const [inView, setInView] = useState(eager);
  const [retryKey, setRetryKey] = useState(0);

  const sessionRef = useRef(0);
  const teardownRef = useRef<(() => void) | null>(null);
  const trackedStreamRef = useRef<string | null>(null);
  const slotHeldRef = useRef(false);

  const releaseSlot = () => {
    if (slotHeldRef.current) {
      releaseGo2RtcSlot();
      slotHeldRef.current = false;
    }
  };

  const stopPlayer = (unregister = true) => {
    const stream = trackedStreamRef.current;
    teardownRef.current?.();
    teardownRef.current = null;
    if (unregister && stream) {
      unregisterUiConsumer(stream);
      trackedStreamRef.current = null;
    }
    releaseSlot();
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
      setIsQueued(false);
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
      setIsConnecting(false);
      setIsQueued(true);
      setError(null);
      setStreamStatus('idle');

      await acquireGo2RtcSlot();
      if (cancelled || session !== sessionRef.current) {
        releaseGo2RtcSlot();
        setIsQueued(false);
        return;
      }
      slotHeldRef.current = true;
      setIsQueued(false);
      setIsConnecting(true);
      setStreamStatus('connecting');

      let attempt = 0;
      while (!cancelled && session === sessionRef.current) {
        const mode = modes[Math.min(attempt, modes.length - 1)];
        const timeoutMs = attempt === 0 ? CONNECT_TIMEOUT_MS : RETRY_TIMEOUT_MS;

        try {
          const cleanup = await waitForFirstFrame(
            container,
            stream,
            mode,
            timeoutMs,
            camera.workerId,
          );
          if (cancelled || session !== sessionRef.current) {
            cleanup();
            return;
          }

          // Free the connect slot so later tiles (last cam in the grid) can start.
          releaseSlot();

          registerUiConsumer(stream);
          trackedStreamRef.current = stream;
          teardownRef.current = cleanup;
          setIsConnecting(false);
          setStreamStatus('playing');
          setError(null);
          reportGo2RtcClientOk({
            cameraId: camera.id,
            cameraUid: camera.cameraUid,
            stream,
          });
          return;
        } catch (err) {
          stopPlayer(true);
          if (cancelled || session !== sessionRef.current) return;

          const raw = err instanceof Error ? err.message : 'Failed to connect';
          // Never show timeout/error text on live tiles — keep Connecting… and retry.
          setError(null);
          setIsConnecting(true);
          setStreamStatus('connecting');

          if (!isTemporaryError(raw) && isNonRetryableError(raw)) {
            reportGo2RtcClientError({
              cameraId: camera.id,
              cameraUid: camera.cameraUid,
              stream,
              message: raw,
            });
          }

          const delayMs = isTemporaryError(raw) ? 1500 : 4000;
          await new Promise((r) => setTimeout(r, delayMs));
          await acquireGo2RtcSlot();
          if (cancelled || session !== sessionRef.current) {
            releaseGo2RtcSlot();
            return;
          }
          slotHeldRef.current = true;
          attempt += 1;
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
      setIsQueued(false);
      setStreamStatus('idle');
    };
  }, [
    camera?.id,
    camera?.cameraUid,
    camera?.name,
    camera?.displayName,
    camera?.online,
    camera?.workerId,
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
    isQueued,
    error,
    streamStatus,
    inView: eager || inView,
    streamName: camera ? go2rtcStreamName(camera.cameraUid || camera.id, profile) : null,
    retry: () => setRetryKey((k) => k + 1),
  };
}
