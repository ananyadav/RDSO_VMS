import { type RefObject } from 'react';
import { useLiveConfig } from '../lib/liveProvider';
import { useGo2RtcLive } from './useGo2RtcLive';
import { useLiveHLS } from './useLiveHLS';

interface Camera {
  id: string;
  name: string;
  online: boolean;
  ptz: boolean;
  activity: boolean;
  cameraUid?: string;
}

interface UseLiveStreamOptions {
  /** Visibility observer for lazy grid tiles. */
  observeRef?: RefObject<HTMLElement | null>;
  /** go2rtc player mount node (separate from overlays). */
  playerContainerRef?: RefObject<HTMLElement | null>;
  containerRef?: RefObject<HTMLElement | null>;
  profile?: 'sub' | 'main';
  fullscreen?: boolean;
  eager?: boolean;
  active?: boolean;
  forceSub?: boolean;
  sessionKey?: number;
  streamsReady?: boolean;
}

/** Unified live hook — go2rtc default, HLS emergency fallback via LIVE_PROVIDER=hls. */
export function useLiveStream(camera: Camera | null, options?: UseLiveStreamOptions) {
  const config = useLiveConfig();
  const useHls = config.provider === 'hls';

  // Grid tiles always use sub/102 — main/101 is fullscreen-only.
  const profile = options?.fullscreen
    ? options?.forceSub
      ? 'sub'
      : (options?.profile ?? 'main')
    : 'sub';
  const playerContainerRef = options?.playerContainerRef ?? options?.containerRef ?? { current: null };
  const observeRef = options?.observeRef ?? options?.containerRef;

  const go2rtc = useGo2RtcLive(camera, {
    containerRef: playerContainerRef,
    observeRef,
    profile,
    eager: options?.eager ?? options?.fullscreen,
    active: useHls ? false : options?.active !== false,
    sessionKey: options?.sessionKey,
    streamsReady: options?.streamsReady,
  });

  const hls = useLiveHLS(camera, {
    containerRef: options?.containerRef,
    fullscreen: options?.fullscreen,
    eager: options?.eager,
    forceSub: options?.forceSub,
    sessionKey: options?.sessionKey,
    disabled: !useHls,
  });

  if (useHls) {
    return {
      provider: 'hls' as const,
      containerRef: hls.videoRef,
      videoRef: hls.videoRef,
      isConnecting: hls.isConnecting,
      error: hls.error,
      streamStatus: hls.streamStatus,
      inView: hls.inView,
      tileBadge: hls.tileBadge,
      jumpingToLive: hls.jumpingToLive,
      startupPhase: hls.startupPhase,
      fullscreenTimedOut: hls.fullscreenTimedOut,
      streamLabel: hls.streamLabel,
    };
  }

  return {
    provider: 'go2rtc' as const,
    playerContainerRef,
    videoRef: null as unknown as RefObject<HTMLVideoElement | null>,
    isConnecting: go2rtc.isConnecting,
    error: go2rtc.error,
    streamStatus: go2rtc.streamStatus,
    inView: go2rtc.inView,
    tileBadge: 'none' as const,
    jumpingToLive: false,
    startupPhase: 'idle' as const,
    fullscreenTimedOut: false,
    streamLabel: go2rtc.streamName,
  };
}
