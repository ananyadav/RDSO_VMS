/**
 * Development-only Live View stream-start latency instrumentation.
 * Generic stage names — reusable for recorder / analytics workers later.
 */

import { go2rtcWsPath, normalizeWorkerId } from './mediaUrls';

const isDev = Boolean(import.meta.env.DEV);

export type LiveLatencyStatus = 'complete' | 'cancelled' | 'error';

export interface LiveLatencyRecord {
  id: string;
  status: LiveLatencyStatus;
  cameraId: string;
  cameraUid?: string;
  workerId: number | null;
  profile: 'sub' | 'main';
  /** Path only — no query string / credentials. */
  mediaPath: string | null;
  timestamp: string;
  t0: number | null;
  t1: number | null;
  t2: number | null;
  t3: number | null;
  t4: number | null;
  t5: number | null;
  t6: number | null;
  t7: number | null;
  queue_wait_ms: number | null;
  player_start_ms: number | null;
  transport_ms: number | null;
  metadata_ms: number | null;
  playing_ms: number | null;
  first_frame_ms: number | null;
  total_visible_ms: number | null;
  transportEvent: string | null;
  cancelReason?: string;
  errorMessage?: string;
}

export interface LiveLatencySummary {
  count: number;
  cancelledCount: number;
  first_frame_ms: { p50: number | null; p95: number | null; max: number | null };
  queue_wait_ms: { p50: number | null; p95: number | null };
  metadata_ms: { p50: number | null; p95: number | null };
  slowest: Array<{
    cameraId: string;
    workerId: number | null;
    first_frame_ms: number | null;
    queue_wait_ms: number | null;
    total_visible_ms: number | null;
  }>;
}

function delta(end: number | null, start: number | null): number | null {
  if (end == null || start == null) return null;
  return Math.round(end - start);
}

function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return Math.round(sorted[Math.max(0, idx)]);
}

const completedSamples: LiveLatencyRecord[] = [];
const cancelledSamples: LiveLatencyRecord[] = [];

let sessionCounter = 0;

export class LiveLatencySession {
  readonly id: string;
  readonly cameraId: string;
  readonly cameraUid?: string;
  readonly workerId: number | null;
  readonly profile: 'sub' | 'main';
  readonly mediaPath: string | null;

  t0: number | null = null;
  t1: number | null = null;
  t2: number | null = null;
  t3: number | null = null;
  t4: number | null = null;
  t5: number | null = null;
  t6: number | null = null;
  t7: number | null = null;
  transportEvent: string | null = null;

  private finalized = false;

  constructor(meta: {
    cameraId: string;
    cameraUid?: string;
    workerId?: number | string | null;
    profile: 'sub' | 'main';
    stream: string;
  }) {
    sessionCounter += 1;
    this.id = `live-${sessionCounter}-${Date.now()}`;
    this.cameraId = meta.cameraId;
    this.cameraUid = meta.cameraUid;
    this.workerId = normalizeWorkerId(meta.workerId);
    this.profile = meta.profile;
    try {
      this.mediaPath = go2rtcWsPath(this.workerId ?? undefined);
    } catch {
      this.mediaPath = null;
    }
  }

  markT0(): void {
    if (this.t0 == null) this.t0 = performance.now();
  }

  markT1(): void {
    this.t1 = performance.now();
  }

  markT2(): void {
    this.t2 = performance.now();
  }

  markT3(): void {
    this.t3 = performance.now();
  }

  markT4(event: string): void {
    if (this.t4 != null) return;
    this.t4 = performance.now();
    this.transportEvent = event;
  }

  markT5(): void {
    if (this.t5 != null) return;
    this.t5 = performance.now();
  }

  markT6(): void {
    if (this.t6 != null) return;
    this.t6 = performance.now();
  }

  markT7(): void {
    if (this.t7 != null) return;
    this.t7 = performance.now();
    this.finalize('complete');
  }

  /** Clear player-stage timestamps before a connect retry (keeps T0). */
  resetAttempt(): void {
    this.t3 = null;
    this.t4 = null;
    this.t5 = null;
    this.t6 = null;
    this.t7 = null;
    this.transportEvent = null;
  }

  cancel(reason = 'unmounted'): void {
    if (this.finalized) return;
    this.finalize('cancelled', reason);
  }

  fail(message: string): void {
    if (this.finalized) return;
    this.finalize('error', undefined, message);
  }

  private compute(): Omit<LiveLatencyRecord, 'id' | 'status' | 'cancelReason' | 'errorMessage'> {
    return {
      cameraId: this.cameraId,
      cameraUid: this.cameraUid,
      workerId: this.workerId,
      profile: this.profile,
      mediaPath: this.mediaPath,
      timestamp: new Date().toISOString(),
      t0: this.t0,
      t1: this.t1,
      t2: this.t2,
      t3: this.t3,
      t4: this.t4,
      t5: this.t5,
      t6: this.t6,
      t7: this.t7,
      queue_wait_ms: delta(this.t2, this.t1),
      player_start_ms: delta(this.t3, this.t2),
      transport_ms: delta(this.t4, this.t3),
      metadata_ms: delta(this.t5, this.t3),
      playing_ms: delta(this.t6, this.t3),
      first_frame_ms: delta(this.t7, this.t3),
      total_visible_ms: delta(this.t7, this.t0),
      transportEvent: this.transportEvent,
    };
  }

  private finalize(
    status: LiveLatencyStatus,
    cancelReason?: string,
    errorMessage?: string,
  ): void {
    if (this.finalized) return;
    this.finalized = true;

    const record: LiveLatencyRecord = {
      id: this.id,
      status,
      ...this.compute(),
      cancelReason,
      errorMessage,
    };

    if (status === 'complete') {
      completedSamples.push(record);
      logLatency(record);
    } else if (status === 'cancelled') {
      cancelledSamples.push(record);
    } else {
      cancelledSamples.push(record);
    }
  }
}

function logLatency(record: LiveLatencyRecord): void {
  if (!isDev) return;
  const fmt = (v: number | null) => (v == null ? 'n/a' : `${v}ms`);
  console.info(
    [
      '[live-latency]',
      `camera=${record.cameraId}`,
      `worker=${record.workerId ?? 'n/a'}`,
      `queue=${fmt(record.queue_wait_ms)}`,
      `player=${fmt(record.player_start_ms)}`,
      `metadata=${fmt(record.metadata_ms)}`,
      `playing=${fmt(record.playing_ms)}`,
      `firstFrame=${fmt(record.first_frame_ms)}`,
      `total=${fmt(record.total_visible_ms)}`,
    ].join('\n'),
  );
}

function buildSummary(): LiveLatencySummary {
  const complete = completedSamples.filter((s) => s.status === 'complete');
  const firstFrames = complete
    .map((s) => s.first_frame_ms)
    .filter((v): v is number => v != null);
  const queueWaits = complete
    .map((s) => s.queue_wait_ms)
    .filter((v): v is number => v != null);
  const metadata = complete
    .map((s) => s.metadata_ms)
    .filter((v): v is number => v != null);

  const slowest = [...complete]
    .sort((a, b) => (b.first_frame_ms ?? 0) - (a.first_frame_ms ?? 0))
    .slice(0, 5)
    .map((s) => ({
      cameraId: s.cameraId,
      workerId: s.workerId,
      first_frame_ms: s.first_frame_ms,
      queue_wait_ms: s.queue_wait_ms,
      total_visible_ms: s.total_visible_ms,
    }));

  return {
    count: complete.length,
    cancelledCount: cancelledSamples.length,
    first_frame_ms: {
      p50: percentile(firstFrames, 50),
      p95: percentile(firstFrames, 95),
      max: firstFrames.length ? Math.max(...firstFrames) : null,
    },
    queue_wait_ms: {
      p50: percentile(queueWaits, 50),
      p95: percentile(queueWaits, 95),
    },
    metadata_ms: {
      p50: percentile(metadata, 50),
      p95: percentile(metadata, 95),
    },
    slowest,
  };
}

export function createLiveLatencySession(meta: {
  cameraId: string;
  cameraUid?: string;
  workerId?: number | string | null;
  profile: 'sub' | 'main';
  stream: string;
}): LiveLatencySession | null {
  if (!isDev) return null;
  return new LiveLatencySession(meta);
}

export const nvrLiveMetrics = {
  getAll(): { complete: LiveLatencyRecord[]; cancelled: LiveLatencyRecord[] } {
    return {
      complete: [...completedSamples],
      cancelled: [...cancelledSamples],
    };
  },
  clear(): void {
    completedSamples.length = 0;
    cancelledSamples.length = 0;
  },
  summary(): LiveLatencySummary {
    return buildSummary();
  },
};

if (isDev && typeof window !== 'undefined') {
  (window as unknown as { __nvrLiveMetrics?: typeof nvrLiveMetrics }).__nvrLiveMetrics =
    nvrLiveMetrics;
}
