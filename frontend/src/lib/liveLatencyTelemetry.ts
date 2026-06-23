/**
 * Report live HLS latency metrics to the backend diagnostics store.
 * Measurement only — does not change playback behavior.
 */

import type Hls from 'hls.js';
import { getBufferLengthSec, getLiveDelaySec } from './hlsLiveEdge';

export interface LiveLatencyTelemetryPayload {
  streamId: string;
  profile: 'grid' | 'fullscreen';
  acquireWall?: number;
  manifestLoadedWall?: number;
  videoPlayingWall?: number;
  liveEdgeDelaySec?: number;
  bufferLengthSec?: number;
}

export function reportLiveLatency(payload: LiveLatencyTelemetryPayload): void {
  const body = JSON.stringify({
    streamId: payload.streamId,
    profile: payload.profile,
    acquireWall: payload.acquireWall,
    manifestLoadedWall: payload.manifestLoadedWall,
    videoPlayingWall: payload.videoPlayingWall,
    liveEdgeDelaySec: payload.liveEdgeDelaySec,
    bufferLengthSec: payload.bufferLengthSec,
  });
  try {
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      navigator.sendBeacon('/api/live/telemetry', blob);
      return;
    }
  } catch {
    // fall through to fetch
  }
  fetch('/api/live/telemetry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {});
}

export function measureHlsLiveMetrics(
  hls: Hls,
  video: HTMLVideoElement,
): { liveEdgeDelaySec: number | null; bufferLengthSec: number | null } {
  return {
    liveEdgeDelaySec: getLiveDelaySec(hls, video),
    bufferLengthSec: getBufferLengthSec(video),
  };
}

export function logLatencyEvent(
  streamId: string,
  event: string,
  detail?: Record<string, unknown>,
): void {
  const extra = detail ? ` ${JSON.stringify(detail)}` : '';
  console.info(`[HLS][latency] ${event} streamId=${streamId}${extra}`);
}
