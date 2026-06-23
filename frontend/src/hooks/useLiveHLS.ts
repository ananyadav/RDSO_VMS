import { attachLiveMonitorGuards } from '../lib/liveMonitorVideo';
import { useEffect, useRef, useState, type RefObject } from 'react';
import Hls from 'hls.js';
import {
  acquireLiveStream,
  releaseLiveStream,
  livePlaylistUrl,
  fetchLiveStreamStatus,
  streamKey,
  restartFullscreenStream,
} from '../lib/liveStreamRegistry';
import {
  logLatencyEvent,
  measureHlsLiveMetrics,
  reportLiveLatency,
} from '../lib/liveLatencyTelemetry';
import {
  getLiveDelaySec,
  LIVE_EDGE_MAINTAIN_DELAY_SEC,
  LIVE_EDGE_START_JUMP_SEC,
  seekToLiveEdge,
  shouldJumpToLive,
} from '../lib/hlsLiveEdge';
import {
  CODEC_UNSUPPORTED_MESSAGE,
  codecFromHlsLevels,
  codecFromPlaylistText,
  isCodecRelatedHlsError,
  shouldWarnHevcInBrowser,
  tileBadgeForCodec,
  type TileCodecBadge,
} from '../lib/hlsCodecCompat';

interface Camera {
  id: string;
  name: string;
  online: boolean;
  ptz: boolean;
  activity: boolean;
}

export type LiveStreamStatus = 'idle' | 'connecting' | 'playing' | 'fallback' | 'error';

export type FullscreenStartupPhase =
  | 'idle'
  | 'connecting_main'
  | 'waiting_playlist'
  | 'waiting_segment'
  | 'playing'
  | 'failed_453'
  | 'failed'
  | 'fallback_sub';

export type { TileCodecBadge };

const BLANK_TILE_TIMEOUT_MS = 8000;
const MAX_MEDIA_RECOVERIES = 2;
const FULLSCREEN_START_TIMEOUT_MS = 15000;
const LIVE_EDGE_JUMP_BANNER_MS = 2000;
const LIVE_EDGE_POLL_MS = 3000;

function isRtsp453(message: string | null | undefined): boolean {
  if (!message) return false;
  const m = message.toLowerCase();
  return m.includes('453') || m.includes('not enough bandwidth');
}

interface UseLiveHLSOptions {
  containerRef?: RefObject<HTMLElement | null>;
  eager?: boolean;
  /** Grid tile — substream 102 (`cameraId`) */
  fullscreen?: boolean;
  /** @deprecated use fullscreen */
  viewOnly?: boolean;
  /** User-selected sub/102 fullscreen (manual fallback) */
  forceSub?: boolean;
  /** Bump to retry fullscreen attach */
  sessionKey?: number;
  /** When true, no HLS connection is made (go2rtc is active). */
  disabled?: boolean;
}

function bustedPlaylistUrl(url: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}_=${Date.now()}`;
}

function resetVideoElement(video: HTMLVideoElement) {
  video.pause();
  video.removeAttribute('src');
  video.load();
}

function createHls(): Hls {
  return new Hls({
    enableWorker: true,
    lowLatencyMode: true,
    liveSyncDurationCount: 1,
    liveMaxLatencyDurationCount: 3,
    maxBufferLength: 4,
    maxMaxBufferLength: 6,
    backBufferLength: 0,
    maxLiveSyncPlaybackRate: 1.5,
    maxBufferHole: 0.5,
    liveDurationInfinity: true,
    manifestLoadingMaxRetry: 8,
    manifestLoadingRetryDelay: 300,
    levelLoadingMaxRetry: 6,
    fragLoadingMaxRetry: 8,
    fragLoadingRetryDelay: 400,
  });
}

function syncAndPlayAtLiveEdge(
  hls: Hls,
  video: HTMLVideoElement,
  onJump: (delaySec: number) => void,
): void {
  const delayBefore = getLiveDelaySec(hls, video);
  seekToLiveEdge(hls, video);
  const delayAfter = getLiveDelaySec(hls, video);
  const delay = delayAfter ?? delayBefore;
  if (delay != null && delay > LIVE_EDGE_START_JUMP_SEC) {
    onJump(delay);
  }
  video.play().catch(() => {});
}

async function playlistReady(url: string, signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(bustedPlaylistUrl(url), {
      method: 'GET',
      cache: 'no-store',
      signal,
    });
    if (!res.ok) return false;
    const text = await res.text();
    return text.includes('#EXTM3U') && text.includes('.ts');
  } catch {
    return false;
  }
}

async function waitForPlaylist(
  url: string,
  maxMs: number,
  signal: AbortSignal,
): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < maxMs) {
    if (signal.aborted) return false;
    if (await playlistReady(url, signal)) return true;
    await new Promise((r) => setTimeout(r, 80));
  }
  return false;
}

function destroyHlsInstance(
  hls: Hls | null,
  video: HTMLVideoElement | null,
): Hls | null {
  if (hls) {
    try {
      hls.stopLoad();
      hls.detachMedia();
      hls.destroy();
    } catch {
      // ignore teardown races
    }
  }
  if (video) {
    resetVideoElement(video);
  }
  return null;
}

export const useLiveHLS = (camera: Camera | null, options?: UseLiveHLSOptions) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const boundVideoRef = useRef<HTMLVideoElement | null>(null);
  const liveMonitorCleanupRef = useRef<(() => void) | null>(null);
  const sessionRef = useRef(0);

  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamStatus, setStreamStatus] = useState<LiveStreamStatus>('idle');
  const [streamLabel, setStreamLabel] = useState<string | null>(null);
  const [tileBadge, setTileBadge] = useState<TileCodecBadge>('none');
  const [isCodecError, setIsCodecError] = useState(false);
  const [inView, setInView] = useState(false);
  const [startupPhase, setStartupPhase] = useState<FullscreenStartupPhase>('idle');
  const [fullscreenTimedOut, setFullscreenTimedOut] = useState(false);
  const [jumpingToLive, setJumpingToLive] = useState(false);

  const fullscreen = Boolean(options?.fullscreen ?? options?.viewOnly);
  const forceSub = Boolean(options?.forceSub);
  const sessionKey = options?.sessionKey ?? 0;
  const eager = fullscreen || options?.eager === true;
  const lazyLoad = !eager && !fullscreen;
  const disabled = Boolean(options?.disabled);
  const shouldStream =
    !disabled && Boolean(camera?.online) && (eager || fullscreen || inView);

  useEffect(() => {
    if (!lazyLoad || !options?.containerRef) {
      setInView(true);
      return;
    }

    const el = options.containerRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => setInView(entry.isIntersecting),
      { root: null, rootMargin: '200px', threshold: 0.01 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [camera?.id, lazyLoad, options?.containerRef]);

  useEffect(() => {
    if (!camera || !shouldStream) {
      setIsConnecting(false);
      setError(null);
      setStreamStatus('idle');
      setStartupPhase('idle');
      setFullscreenTimedOut(false);
      setJumpingToLive(false);
      setTileBadge('none');
      setIsCodecError(false);
      return;
    }

    const cameraId = camera.id;
    const sid = streamKey(cameraId, fullscreen);
    const profile = fullscreen ? 'fullscreen' : 'grid';
    const acquireWall = Date.now() / 1000;
    const playlistUrl = livePlaylistUrl(cameraId, fullscreen);
    const session = ++sessionRef.current;
    const ac = new AbortController();
    let recoverTimer: ReturnType<typeof setTimeout> | null = null;
    let statusTimer: ReturnType<typeof setInterval> | null = null;
    let latencyTimer: ReturnType<typeof setInterval> | null = null;
    let jumpBannerTimer: ReturnType<typeof setTimeout> | null = null;
    let blankTileTimer: ReturnType<typeof setTimeout> | null = null;
    let mediaRecoveries = 0;
    let acquired = false;
    let hasStartedPlaying = false;
    let manifestReported = false;
    let playingReported = false;

    let fullscreenTimeoutTimer: ReturnType<typeof setTimeout> | null = null;

    const pushTelemetry = (extra: {
      manifestLoadedWall?: number;
      videoPlayingWall?: number;
      liveEdgeDelaySec?: number | null;
      bufferLengthSec?: number | null;
    }) => {
      reportLiveLatency({
        streamId: sid,
        profile,
        acquireWall,
        manifestLoadedWall: extra.manifestLoadedWall,
        videoPlayingWall: extra.videoPlayingWall,
        liveEdgeDelaySec: extra.liveEdgeDelaySec ?? undefined,
        bufferLengthSec: extra.bufferLengthSec ?? undefined,
      });
    };

    const reportPlaying = (video: HTMLVideoElement, hls: Hls | null) => {
      if (playingReported) return;
      playingReported = true;
      const wall = Date.now() / 1000;
      const startupMs = Math.round((wall - acquireWall) * 1000);
      let liveEdgeDelaySec: number | null = null;
      let bufferLengthSec: number | null = null;
      if (hls) {
        const m = measureHlsLiveMetrics(hls, video);
        liveEdgeDelaySec = m.liveEdgeDelaySec;
        bufferLengthSec = m.bufferLengthSec;
      }
      logLatencyEvent(sid, 'video playing', {
        startupMs,
        liveEdgeDelaySec,
        bufferLengthSec,
      });
      pushTelemetry({
        videoPlayingWall: wall,
        liveEdgeDelaySec,
        bufferLengthSec,
      });
    };

    const reportManifest = () => {
      if (manifestReported) return;
      manifestReported = true;
      const wall = Date.now() / 1000;
      logLatencyEvent(sid, 'manifest loaded', {
        msSinceAcquire: Math.round((wall - acquireWall) * 1000),
      });
      pushTelemetry({ manifestLoadedWall: wall });
    };

    const showJumpingToLive = (delaySec?: number) => {
      setJumpingToLive(true);
      if (jumpBannerTimer) clearTimeout(jumpBannerTimer);
      jumpBannerTimer = setTimeout(() => setJumpingToLive(false), LIVE_EDGE_JUMP_BANNER_MS);
      logLatencyEvent(sid, 'jumping to live', { delaySec });
    };

    const maintainLiveEdge = (hls: Hls, video: HTMLVideoElement) => {
      if (!shouldJumpToLive(hls, video, LIVE_EDGE_MAINTAIN_DELAY_SEC)) return;
      const delay = getLiveDelaySec(hls, video);
      if (seekToLiveEdge(hls, video)) {
        showJumpingToLive(delay ?? undefined);
      }
    };

    const startLiveEdgePolling = (video: HTMLVideoElement, hls: Hls) => {
      if (latencyTimer) clearInterval(latencyTimer);
      latencyTimer = setInterval(() => {
        if (isStale() || hlsRef.current !== hls) return;
        maintainLiveEdge(hls, video);
        const { liveEdgeDelaySec, bufferLengthSec } = measureHlsLiveMetrics(hls, video);
        if (liveEdgeDelaySec != null || bufferLengthSec != null) {
          logLatencyEvent(sid, 'live delay', { liveEdgeDelaySec, bufferLengthSec });
          pushTelemetry({
            videoPlayingWall: playingReported ? Date.now() / 1000 : undefined,
            liveEdgeDelaySec,
            bufferLengthSec,
          });
        }
      }, LIVE_EDGE_POLL_MS);
    };

    const isStale = () => ac.signal.aborted || session !== sessionRef.current;

    const setPhase = (phase: FullscreenStartupPhase) => {
      if (fullscreen) setStartupPhase(phase);
    };

    const failFullscreen453 = (message: string) => {
      setPhase('failed_453');
      failStreamError(null, null, message, 'error');
    };

    const failCodecPlayback = (video: HTMLVideoElement | null, hls: Hls | null, message?: string) => {
      setIsCodecError(true);
      setTileBadge('h265-warning');
      setError(message ?? CODEC_UNSUPPORTED_MESSAGE);
      setIsConnecting(false);
      setStreamStatus('error');
      if (hls && video) {
        hlsRef.current = destroyHlsInstance(hls, video);
        boundVideoRef.current = null;
      } else if (video) {
        resetVideoElement(video);
      }
    };

    const failStreamError = (
      video: HTMLVideoElement | null,
      hls: Hls | null,
      message: string,
      badge: TileCodecBadge = 'error',
    ) => {
      setIsCodecError(false);
      setTileBadge(badge);
      setError(message);
      setIsConnecting(false);
      setStreamStatus('error');
      if (hls && video) {
        hlsRef.current = destroyHlsInstance(hls, video);
        boundVideoRef.current = null;
      } else if (video) {
        resetVideoElement(video);
      }
    };

    const applyCodecFromManifest = (hls: Hls, video: HTMLVideoElement): boolean => {
      const codec = codecFromHlsLevels(hls);
      if (shouldWarnHevcInBrowser(codec)) {
        failCodecPlayback(video, hls);
        return false;
      }
      const badge = tileBadgeForCodec(codec);
      if (badge !== 'none') {
        setTileBadge(badge);
      }
      return true;
    };

    const startBlankTileWatch = (video: HTMLVideoElement, hls: Hls) => {
      if (blankTileTimer) clearTimeout(blankTileTimer);
      blankTileTimer = setTimeout(() => {
        if (isStale() || hlsRef.current !== hls) return;
        if (video.videoWidth > 0) return;
        const codec = codecFromHlsLevels(hls);
        if (shouldWarnHevcInBrowser(codec)) {
          failCodecPlayback(video, hls);
        } else {
          failStreamError(
            video,
            hls,
            'No video frames decoded. Check camera substream 102 is H.264.',
            'error',
          );
        }
      }, BLANK_TILE_TIMEOUT_MS);
    };

    const clearBlankTileWatch = () => {
      if (blankTileTimer) {
        clearTimeout(blankTileTimer);
        blankTileTimer = null;
      }
    };

    const pollServerStatus = async () => {
      if (isStale() || !fullscreen) return;
      const info = await fetchLiveStreamStatus(cameraId, true);
      if (isStale() || !info) return;
      if (info.lastError) {
        if (isRtsp453(info.lastError)) {
          failFullscreen453('Failed: RTSP 453 / camera limit');
        } else {
          setStreamStatus('error');
          setError(info.lastError);
          setPhase('failed');
        }
        return;
      }
      if (info.fallback || info.streamLabel?.startsWith('sub')) {
        setStreamStatus('fallback');
        setStreamLabel(info.streamLabel);
        setPhase('fallback_sub');
      } else if (info.ready) {
        setStreamLabel(info.streamLabel);
        if (!hasStartedPlaying) {
          setPhase('waiting_segment');
        }
      }
    };

    const teardownPlayer = () => {
      if (liveMonitorCleanupRef.current) {
        liveMonitorCleanupRef.current();
        liveMonitorCleanupRef.current = null;
      }
      if (recoverTimer) {
        clearTimeout(recoverTimer);
        recoverTimer = null;
      }
      if (statusTimer) {
        clearInterval(statusTimer);
        statusTimer = null;
      }
      if (latencyTimer) {
        clearInterval(latencyTimer);
        latencyTimer = null;
      }
      if (jumpBannerTimer) {
        clearTimeout(jumpBannerTimer);
        jumpBannerTimer = null;
      }
      setJumpingToLive(false);
      if (fullscreenTimeoutTimer) {
        clearTimeout(fullscreenTimeoutTimer);
        fullscreenTimeoutTimer = null;
      }
      clearBlankTileWatch();
      const video = boundVideoRef.current ?? videoRef.current;
      hlsRef.current = destroyHlsInstance(hlsRef.current, video);
      boundVideoRef.current = null;
    };

    const unsubscribe = () => {
      if (acquired) {
        releaseLiveStream(cameraId, fullscreen);
        acquired = false;
      }
    };

    const cleanup = () => {
      ac.abort();
      teardownPlayer();
      unsubscribe();
    };

    const bindLiveMonitor = (video: HTMLVideoElement) => {
      liveMonitorCleanupRef.current?.();
      liveMonitorCleanupRef.current = attachLiveMonitorGuards(video, () => !isStale());
    };

    const attachHls = (video: HTMLVideoElement) => {
      if (isStale()) return;

      if (hlsRef.current && boundVideoRef.current === video) {
        return;
      }

      teardownPlayer();
      resetVideoElement(video);
      bindLiveMonitor(video);
      mediaRecoveries = 0;
      setIsCodecError(false);
      setTileBadge('none');

      if (!Hls.isSupported()) {
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = bustedPlaylistUrl(playlistUrl);
          video.addEventListener(
            'loadedmetadata',
            () => {
              if (isStale()) return;
              reportManifest();
              if (video.videoWidth === 0) {
                setTileBadge('error');
                setError('No video frames decoded. Check substream 102 is H.264.');
                setStreamStatus('error');
                setIsConnecting(false);
                return;
              }
              setIsConnecting(false);
              setStreamStatus('playing');
              setTileBadge('h264');
              setError(null);
              video.play().catch(() => {});
              video.addEventListener('playing', () => reportPlaying(video, null), { once: true });
              void pollServerStatus();
            },
            { once: true },
          );
          video.addEventListener(
            'error',
            () => {
              if (isStale()) return;
              setIsCodecError(true);
              setTileBadge('h265-warning');
              setError(CODEC_UNSUPPORTED_MESSAGE);
              setStreamStatus('error');
              setIsConnecting(false);
            },
            { once: true },
          );
        } else {
          failStreamError(video, null, 'HLS not supported');
        }
        boundVideoRef.current = video;
        return;
      }

      const hls = createHls();
      hlsRef.current = hls;
      boundVideoRef.current = video;

      hls.loadSource(bustedPlaylistUrl(playlistUrl));
      hls.attachMedia(video);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        if (isStale() || hlsRef.current !== hls) return;
        reportManifest();
        if (!applyCodecFromManifest(hls, video)) return;
        syncAndPlayAtLiveEdge(hls, video, showJumpingToLive);
        setIsConnecting(false);
        setStreamStatus('playing');
        setPhase('playing');
        setFullscreenTimedOut(false);
        hasStartedPlaying = true;
        setError(null);
        startBlankTileWatch(video, hls);
        video.addEventListener('playing', () => {
          reportPlaying(video, hls);
          startLiveEdgePolling(video, hls);
        }, { once: true });
        void pollServerStatus();
      });

      hls.on(Hls.Events.LEVEL_LOADED, () => {
        if (isStale() || hlsRef.current !== hls) return;
        const delayBefore = getLiveDelaySec(hls, video);
        if (seekToLiveEdge(hls, video)) {
          const delay = getLiveDelaySec(hls, video) ?? delayBefore;
          if (delay != null && delay > LIVE_EDGE_START_JUMP_SEC) {
            showJumpingToLive(delay);
          }
        }
      });

      hls.on(Hls.Events.LEVEL_UPDATED, () => {
        if (isStale() || hlsRef.current !== hls) return;
        if (video.videoWidth > 0) {
          clearBlankTileWatch();
          const badge = tileBadgeForCodec(codecFromHlsLevels(hls));
          if (badge !== 'none') setTileBadge(badge);
        }
      });

      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (isStale() || hlsRef.current !== hls) return;

        if (isCodecRelatedHlsError(data)) {
          failCodecPlayback(video, hls);
          return;
        }

        if (!data.fatal) return;

        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          recoverTimer = setTimeout(() => {
            if (!isStale() && hlsRef.current === hls) {
              hls.startLoad(-1);
            }
          }, 600);
          return;
        }

        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          mediaRecoveries += 1;
          if (mediaRecoveries <= MAX_MEDIA_RECOVERIES) {
            hls.recoverMediaError();
            return;
          }
          failCodecPlayback(video, hls);
          return;
        }

        failStreamError(video, hls, 'Stream error');
      });
    };

    setIsConnecting(true);
    setError(null);
    setStreamStatus('connecting');
    setStreamLabel(null);
    setTileBadge('none');
    setIsCodecError(false);
    setFullscreenTimedOut(false);
    pushTelemetry({});
    logLatencyEvent(sid, 'acquire stream', { profile });
    if (fullscreen) {
      setPhase(forceSub ? 'fallback_sub' : 'connecting_main');
      fullscreenTimeoutTimer = setTimeout(() => {
        if (isStale() || hasStartedPlaying) return;
        setFullscreenTimedOut(true);
      }, FULLSCREEN_START_TIMEOUT_MS);
      statusTimer = setInterval(() => {
        void pollServerStatus();
      }, 2000);
    }

    (async () => {
      try {
        let ok: boolean;
        if (fullscreen && forceSub) {
          ok = await restartFullscreenStream(cameraId, { forceSub: true });
        } else {
          ok = await acquireLiveStream(cameraId, fullscreen);
        }
        if (isStale()) return;
        if (!ok) {
          failStreamError(null, null, 'Failed to start');
          setPhase('failed');
          return;
        }
        acquired = true;

        if (fullscreen) {
          setPhase(forceSub ? 'fallback_sub' : 'connecting_main');
          const info = await fetchLiveStreamStatus(cameraId, true);
          if (!isStale() && info) {
            setStreamLabel(info.streamLabel);
            if (info.fallback || info.streamLabel?.startsWith('sub')) {
              setStreamStatus('fallback');
              setPhase('fallback_sub');
            } else if (isRtsp453(info.lastError)) {
              failFullscreen453('Failed: RTSP 453 / camera limit');
              return;
            }
          }
        }

        setPhase('waiting_playlist');
        let ready = await playlistReady(playlistUrl, ac.signal);
        if (!ready) {
          ready = await waitForPlaylist(
            playlistUrl,
            fullscreen ? 12000 : 5000,
            ac.signal,
          );
        }
        if (isStale()) return;

        if (!ready && fullscreen) {
          setFullscreenTimedOut(true);
          setPhase('failed');
          failStreamError(null, null, 'Playlist not ready');
          return;
        }

        if (ready && !fullscreen) {
          try {
            const probe = await fetch(bustedPlaylistUrl(playlistUrl), {
              cache: 'no-store',
              signal: ac.signal,
            });
            if (probe.ok) {
              const text = await probe.text();
              const codec = codecFromPlaylistText(text);
              if (shouldWarnHevcInBrowser(codec)) {
                failCodecPlayback(null, null);
                return;
              }
            }
          } catch {
            // playlist probe is best-effort
          }
        }

        const video = videoRef.current;
        if (!video) {
          failStreamError(null, null, 'Video not ready');
          setPhase('failed');
          return;
        }

        if (fullscreen) {
          setPhase('waiting_segment');
        }

        attachHls(video);
      } catch {
        if (!isStale()) {
          failStreamError(videoRef.current, null, 'Connection failed');
        }
      }
    })();

    return cleanup;
  }, [camera?.id, camera?.online, shouldStream, fullscreen, eager, forceSub, sessionKey]);

  return {
    videoRef,
    isConnecting,
    error,
    streamStatus,
    streamLabel,
    tileBadge,
    isCodecError,
    streamId: camera ? streamKey(camera.id, fullscreen) : null,
    inView: eager || inView,
    startupPhase,
    fullscreenTimedOut,
    jumpingToLive,
  };
};
