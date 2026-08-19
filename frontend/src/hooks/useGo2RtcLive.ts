import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { go2rtcStreamName, reportGo2RtcClientError, reportGo2RtcClientOk } from '../lib/liveProvider';
import {
  acquireGo2RtcSlot,
  isGo2RtcSlotAbortError,
  releaseGo2RtcSlot,
} from '../lib/go2rtcConnectionLimiter';
import { createLiveLatencySession, type LiveLatencySession } from '../lib/liveLatencyMetrics';
import { registerUiConsumer, unregisterUiConsumer } from '../lib/go2rtcConsumerRegistry';
import { ensureGo2RtcPlayer, mountGo2RtcPlayer } from '../lib/go2rtcPlayer';

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
/** Visible tile got "playing" or WS open but no usable frame — one bounded reconnect. */
const POST_PLAY_STALL_MS = 4500;
const MAX_POST_PLAY_RETRIES = 1;
const PLAYBACK_MONITOR_INTERVAL_MS = 400;

interface UseGo2RtcLiveOptions {
  containerRef: RefObject<HTMLElement | null>;
  observeRef?: RefObject<HTMLElement | null>;
  /** Scroll/viewport root for IntersectionObserver (e.g. live grid scroller). */
  observeRootRef?: RefObject<HTMLElement | null>;
  profile: 'sub' | 'main';
  active?: boolean;
  eager?: boolean;
  /**
   * Parent stream-eligibility gate (live grid strictly-visible rows).
   * When false, never start or keep a go2rtc player — even if IntersectionObserver
   * reports the mounted overscan tile as intersecting (fullscreen root quirk).
   * Default true so non-grid callers keep observer-based connect.
   */
  streamEligible?: boolean;
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
  signal?: AbortSignal,
  latencySession?: LiveLatencySession | null,
): Promise<() => void> {
  if (signal?.aborted) {
    throw new DOMException('go2rtc connect aborted', 'AbortError');
  }

  return new Promise((resolve, reject) => {
    let cleanup: (() => void) | null = null;
    let frameSeen = false;

    const onAbort = () => {
      clearTimeout(timeoutId);
      cleanup?.();
      cleanup = null;
      reject(new DOMException('go2rtc connect aborted', 'AbortError'));
    };

    const timeoutId = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      cleanup?.();
      reject(new Error('Connection timed out'));
    }, timeoutMs);

    const finish = () => {
      clearTimeout(timeoutId);
      signal?.removeEventListener('abort', onAbort);
      if (cleanup) resolve(cleanup);
    };

    signal?.addEventListener('abort', onAbort, { once: true });

    void mountGo2RtcPlayer(container, {
      stream,
      mode,
      workerId,
      latencySession,
      onFirstFrame: () => {
        frameSeen = true;
        finish();
      },
      onError: (msg: string) => {
        clearTimeout(timeoutId);
        signal?.removeEventListener('abort', onAbort);
        cleanup?.();
        reject(new Error(msg || 'go2rtc playback error'));
      },
    })
      .then((fn) => {
        if (signal?.aborted) {
          fn();
          onAbort();
          return;
        }
        cleanup = fn;
        if (frameSeen) finish();
      })
      .catch((err: unknown) => {
        clearTimeout(timeoutId);
        signal?.removeEventListener('abort', onAbort);
        reject(err instanceof Error ? err : new Error('Failed to load go2rtc player'));
      });
  });
}

export function useGo2RtcLive(camera: Camera | null, options: UseGo2RtcLiveOptions) {
  const containerRef = options.containerRef;
  const observeRef = options.observeRef ?? containerRef;
  const observeRootRef = options.observeRootRef;
  const profile = options.profile;
  const active = options.active !== false;
  const eager = options.eager === true;
  const streamEligible = options.streamEligible !== false;
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
  const latencySessionRef = useRef<LiveLatencySession | null>(null);
  const playbackMonitorRef = useRef<number | null>(null);
  const postPlayRetriesRef = useRef(0);
  const lastVideoFrameAtRef = useRef(0);

  const clearPlaybackMonitor = () => {
    if (playbackMonitorRef.current != null) {
      window.clearInterval(playbackMonitorRef.current);
      playbackMonitorRef.current = null;
    }
  };

  const gridDebug = (msg: string) => {
    if (import.meta.env.DEV) {
      console.info(`[grid-debug] camera=${camera?.id ?? '?'} ${msg}`);
    }
  };

  const releaseSlot = (label?: string) => {
    if (slotHeldRef.current) {
      releaseGo2RtcSlot(label);
      slotHeldRef.current = false;
    }
  };

  const stopPlayer = (unregister = true, label?: string) => {
    clearPlaybackMonitor();
    const stream = trackedStreamRef.current;
    teardownRef.current?.();
    teardownRef.current = null;
    if (unregister && stream) {
      unregisterUiConsumer(stream);
      trackedStreamRef.current = null;
    }
    releaseSlot(label);
  };

  useLayoutEffect(() => {
    if (eager) {
      setInView(true);
      return;
    }

    // Overscan / parent-ineligible: keep the card mounted but do not treat IO
    // intersection as permission to stream (Control Room fullscreen can mark
    // clipped overscan tiles as intersecting).
    setInView(false);
    if (!streamEligible) {
      return;
    }

    let cancelled = false;
    let observer: IntersectionObserver | null = null;
    let raf = 0;

    const attach = () => {
      if (cancelled) return;
      const el = observeRef.current;
      if (!el) {
        // Ref not committed yet — retry next frame (avoids permanent black tiles).
        raf = requestAnimationFrame(attach);
        return;
      }
      const root = observeRootRef?.current ?? null;
      observer = new IntersectionObserver(
        ([entry]) => {
          if (cancelled) return;
          // Strict visibility — no large rootMargin, so overscan outside the
          // scroller does not open go2rtc WebSockets.
          setInView(entry.isIntersecting && entry.intersectionRatio > 0);
        },
        { root, rootMargin: '0px', threshold: [0, 0.05, 0.15] },
      );
      observer.observe(el);
    };

    attach();
    return () => {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
      observer?.disconnect();
    };
  }, [camera?.id, eager, streamEligible, observeRef, observeRootRef]);

  useEffect(() => {
    postPlayRetriesRef.current = 0;
    lastVideoFrameAtRef.current = 0;
  }, [camera?.id]);

  useEffect(() => {
    const shouldConnect = Boolean(
      camera?.online && active && streamsReady && streamEligible && inView,
    );

    if (!shouldConnect) {
      postPlayRetriesRef.current = 0;
      stopPlayer(true);
      setIsConnecting(false);
      setIsQueued(false);
      setError(null);
      setStreamStatus('idle');
      return;
    }

    if (!camera) return;

    // playerRef may still be null on the first effect pass after mount.
    if (!containerRef.current) {
      setIsConnecting(true);
      setStreamStatus('connecting');
      gridDebug('playerRef=pending retry=scheduled');
      const raf = requestAnimationFrame(() => setRetryKey((k) => k + 1));
      return () => cancelAnimationFrame(raf);
    }

    const stream = go2rtcStreamName(camera.cameraUid || camera.id, profile);
    const label = camera.id;
    const session = ++sessionRef.current;
    const abortController = new AbortController();
    let cancelled = false;
    const modes: Array<'webrtc' | 'mse'> = ['mse'];

    const isStale = () =>
      cancelled || session !== sessionRef.current || abortController.signal.aborted;

    const run = async () => {
      stopPlayer(true, label);
      setIsConnecting(false);
      setIsQueued(true);
      setError(null);
      setStreamStatus('idle');

      const latencySession = createLiveLatencySession({
        cameraId: camera.id,
        cameraUid: camera.cameraUid,
        workerId: camera.workerId,
        profile,
        stream,
      });
      latencySessionRef.current = latencySession;
      latencySession?.markT0();

      // Start player JS load in parallel with the connection-slot wait so it
      // does not sit on the T2→T3 path after the slot is already held.
      const playerReady = ensureGo2RtcPlayer();

      try {
        latencySession?.markT1();
        await acquireGo2RtcSlot({ signal: abortController.signal, label });
      } catch (err) {
        if (isGo2RtcSlotAbortError(err) || isStale()) {
          latencySession?.cancel('queue-abort');
          latencySessionRef.current = null;
          setIsQueued(false);
          return;
        }
        latencySession?.cancel('queue-error');
        latencySessionRef.current = null;
        setIsQueued(false);
        return;
      }

      // Race: became eligible while unmounting — never start the player.
      if (isStale()) {
        releaseGo2RtcSlot(label);
        latencySession?.cancel('stale-after-queue');
        latencySessionRef.current = null;
        setIsQueued(false);
        return;
      }
      latencySession?.markT2();
      slotHeldRef.current = true;
      setIsQueued(false);
      setIsConnecting(true);
      setStreamStatus('connecting');

      try {
        const ensureStarted = performance.now();
        await playerReady;
        if (import.meta.env.DEV) {
          const ensureMs = Math.round(performance.now() - ensureStarted);
          if (ensureMs >= 20) {
            console.info(
              `[live-latency] post-slot player-ensure camera=${label} ensure_after_slot=${ensureMs}ms`,
            );
          }
        }
      } catch (err) {
        releaseSlot(label);
        latencySession?.fail(err instanceof Error ? err.message : 'Player load failed');
        latencySessionRef.current = null;
        setIsConnecting(false);
        setStreamStatus('error');
        return;
      }

      if (isStale()) {
        releaseSlot(label);
        latencySession?.cancel('stale-after-player-ensure');
        latencySessionRef.current = null;
        return;
      }

      // Container may have remounted between awaits — re-read ref.
      const mountEl = containerRef.current;
      if (!mountEl) {
        releaseSlot(label);
        latencySession?.cancel('missing-container');
        latencySessionRef.current = null;
        const raf = requestAnimationFrame(() => setRetryKey((k) => k + 1));
        void raf;
        return;
      }

      let attempt = 0;
      while (!isStale()) {
        if (abortController.signal.aborted) {
          releaseSlot(label);
          return;
        }

        const mode = modes[Math.min(attempt, modes.length - 1)];
        const timeoutMs = attempt === 0 ? CONNECT_TIMEOUT_MS : RETRY_TIMEOUT_MS;

        try {
          const cleanup = await waitForFirstFrame(
            mountEl,
            stream,
            mode,
            timeoutMs,
            camera.workerId,
            abortController.signal,
            latencySession,
          );
          if (isStale()) {
            cleanup();
            releaseSlot(label);
            latencySession?.cancel('stale-after-frame');
            latencySessionRef.current = null;
            return;
          }

          // Free the connect slot so later tiles (last cam in the grid) can start.
          releaseSlot(label);

          registerUiConsumer(stream);
          trackedStreamRef.current = stream;
          teardownRef.current = cleanup;
          latencySessionRef.current = null;
          setIsConnecting(false);
          setStreamStatus('playing');
          setError(null);
          lastVideoFrameAtRef.current = performance.now();
          gridDebug(
            `mountCalled=1 wsOpened=1 firstFrame=1 profile=${profile} stream=${stream} worker=${camera.workerId ?? '?'}`,
          );

          // Post-play: go2rtc may error internally after first frame; onError is not
          // wired once waitForFirstFrame resolves. Recover visible tiles once.
          clearPlaybackMonitor();
          playbackMonitorRef.current = window.setInterval(() => {
            if (isStale()) return;
            const el = containerRef.current;
            if (!el) return;

            const video = el.querySelector('video');
            const mode =
              el.querySelector('video-stream .mode')?.textContent?.trim() ?? '';
            const statusText =
              el.querySelector('video-stream .status')?.textContent?.trim() ?? '';

            if (video instanceof HTMLVideoElement && video.videoWidth > 0) {
              lastVideoFrameAtRef.current = performance.now();
              return;
            }

            const errored = mode === 'error' || Boolean(statusText);
            const stalled =
              errored ||
              performance.now() - lastVideoFrameAtRef.current > POST_PLAY_STALL_MS;

            if (!stalled || postPlayRetriesRef.current >= MAX_POST_PLAY_RETRIES) {
              return;
            }

            postPlayRetriesRef.current += 1;
            gridDebug(
              `stall-retry=1 metadata=${video?.readyState ?? 'n/a'} playing=${!video?.paused} mode=${mode} error=${statusText || 'none'}`,
            );
            clearPlaybackMonitor();
            stopPlayer(true, label);
            setStreamStatus('connecting');
            setIsConnecting(true);
            setError(null);
            setRetryKey((k) => k + 1);
          }, PLAYBACK_MONITOR_INTERVAL_MS);

          reportGo2RtcClientOk({
            cameraId: camera.id,
            cameraUid: camera.cameraUid,
            stream,
          });
          return;
        } catch (err) {
          if (isGo2RtcSlotAbortError(err) || isStale()) {
            releaseSlot(label);
            latencySession?.cancel('connect-abort');
            latencySessionRef.current = null;
            return;
          }

          stopPlayer(true, label);
          if (isStale()) {
            latencySession?.cancel('stale-on-error');
            latencySessionRef.current = null;
            return;
          }

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
          if (isStale()) {
            latencySession?.cancel('stale-before-retry');
            latencySessionRef.current = null;
            return;
          }

          try {
            latencySession?.markT1();
            await acquireGo2RtcSlot({ signal: abortController.signal, label });
          } catch (acquireErr) {
            if (isGo2RtcSlotAbortError(acquireErr) || isStale()) {
              latencySession?.cancel('retry-queue-abort');
              latencySessionRef.current = null;
              return;
            }
            latencySession?.cancel('retry-queue-error');
            latencySessionRef.current = null;
            return;
          }
          if (isStale()) {
            releaseGo2RtcSlot(label);
            latencySession?.cancel('stale-after-retry-queue');
            latencySessionRef.current = null;
            return;
          }
          latencySession?.markT2();
          latencySession?.resetAttempt();
          slotHeldRef.current = true;
          attempt += 1;
        }
      }
    };

    void run();

    return () => {
      cancelled = true;
      abortController.abort();
      latencySessionRef.current?.cancel('unmounted');
      latencySessionRef.current = null;
      if (session === sessionRef.current) {
        sessionRef.current += 1;
      }
      stopPlayer(true, label);
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
    streamEligible,
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
