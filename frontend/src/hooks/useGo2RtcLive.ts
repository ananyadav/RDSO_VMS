import { useEffect, useRef, useState, type RefObject } from 'react';
import { go2rtcStreamName, resetGo2RtcStreamSync } from '../lib/liveProvider';
import { registerUiConsumer, unregisterUiConsumer } from '../lib/go2rtcConsumerRegistry';
import { mountGo2RtcPlayer } from '../lib/go2rtcPlayer';

interface Camera {
  id: string;
  name: string;
  cameraUid?: string;
  displayName?: string;
  online: boolean;
}

export type Go2RtcStreamStatus = 'idle' | 'connecting' | 'playing' | 'error';

const CONNECT_TIMEOUT_MS = 15000;

interface UseGo2RtcLiveOptions {
  containerRef: RefObject<HTMLElement | null>;
  observeRef?: RefObject<HTMLElement | null>;
  profile: 'sub' | 'main';
  active?: boolean;
  eager?: boolean;
  sessionKey?: number;
  streamsReady?: boolean;
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
  const retriesRef = useRef(0);
  const teardownRef = useRef<(() => void) | null>(null);
  const trackedStreamRef = useRef<string | null>(null);

  const stopPlayer = (unregister = true) => {
    const stream = trackedStreamRef.current;
    teardownRef.current?.();
    teardownRef.current = null;
    if (unregister && stream) {
      unregisterUiConsumer(stream);
      trackedStreamRef.current = null;
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
    if (!container) return;

    const stream = go2rtcStreamName(camera!.cameraUid || camera!.id, profile);
    const session = ++sessionRef.current;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let failed = false;

    const fail = (message: string, canRetry = true) => {
      if (session !== sessionRef.current || failed) return;

      const isNotFound = message.toLowerCase().includes('not found');
      if (canRetry && isNotFound && retriesRef.current < 2) {
        retriesRef.current += 1;
        stopPlayer(true);
        void resetGo2RtcStreamSync().then((ok: boolean) => {
          if (session === sessionRef.current) {
            window.setTimeout(() => setRetryKey((k) => k + 1), ok ? 400 : 1200);
          }
        });
        return;
      }

      failed = true;
      stopPlayer(true);
      setError(message);
      setIsConnecting(false);
      setStreamStatus('error');
    };

    stopPlayer(true);
    retriesRef.current = 0;
    setIsConnecting(true);
    setError(null);
    setStreamStatus('connecting');

    timeoutId = setTimeout(() => {
      fail(`Connection timed out for ${stream} — check camera RTSP or retry`, true);
    }, CONNECT_TIMEOUT_MS);

    void mountGo2RtcPlayer(container, {
      stream,
      mode: 'webrtc',
      onFirstFrame: () => {
        if (session !== sessionRef.current) return;
        if (timeoutId) clearTimeout(timeoutId);
        setIsConnecting(false);
        setStreamStatus('playing');
        setError(null);
      },
      onError: (msg: string) => {
        if (timeoutId) clearTimeout(timeoutId);
        const text = msg.toLowerCase().includes('not found')
          ? `Stream not found: ${stream} — open go2rtc diagnostics and Reload config`
          : msg || 'go2rtc playback error';
        fail(text, true);
      },
    })
      .then((cleanup: () => void) => {
        if (session !== sessionRef.current) {
          cleanup();
          return;
        }
        registerUiConsumer(stream);
        trackedStreamRef.current = stream;
        teardownRef.current = () => {
          cleanup();
        };
      })
      .catch((err: unknown) => {
        if (timeoutId) clearTimeout(timeoutId);
        fail(err instanceof Error ? err.message : 'Failed to load go2rtc player');
      });

    return () => {
      if (session === sessionRef.current) {
        sessionRef.current += 1;
      }
      if (timeoutId) clearTimeout(timeoutId);
      stopPlayer(true);
      setIsConnecting(false);
      setStreamStatus('idle');
    };
  }, [camera?.id, camera?.online, profile, active, inView, containerRef, sessionKey, streamsReady, retryKey]);

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
    streamName: camera ? go2rtcStreamName(camera.id, profile) : null,
  };
}
