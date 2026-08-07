/** Limit concurrent go2rtc *connect* attempts so cameras are not hammered all at once.

Slots are released after the first frame (or on failure) — they do NOT stay held
for the whole playback lifetime. Holding them caused the last camera in every
floor grid to wait behind the first N successful players.

Queued waiters may pass an AbortSignal so unmounted CameraCards leave the queue
immediately and never start a player after cancellation.
*/

const MAX_CONCURRENT = Math.max(
  1,
  Number(import.meta.env.VITE_GO2RTC_MAX_CONCURRENT ?? 16),
);

const isDev = Boolean(import.meta.env.DEV);

function queueLog(message: string): void {
  if (isDev) console.info(`[stream-queue] ${message}`);
}

function abortError(): DOMException {
  return new DOMException('go2rtc connect aborted', 'AbortError');
}

export function isGo2RtcSlotAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === 'AbortError') ||
    (err instanceof Error && err.name === 'AbortError')
  );
}

interface WaitEntry {
  label: string;
  signal?: AbortSignal;
  aborted: boolean;
  resolve: () => void;
  reject: (err: Error) => void;
}

let active = 0;
const waitQueue: WaitEntry[] = [];

export type AcquireGo2RtcSlotOptions = {
  signal?: AbortSignal;
  /** Camera id / stream label for dev diagnostics only. */
  label?: string;
};

function grantImmediate(label: string): void {
  active += 1;
  queueLog(`started camera=${label || '?'} active=${active} queued=${waitQueue.length}`);
}

function removeFromQueue(entry: WaitEntry): boolean {
  const idx = waitQueue.indexOf(entry);
  if (idx < 0) return false;
  waitQueue.splice(idx, 1);
  return true;
}

function promoteNext(): void {
  while (waitQueue.length > 0) {
    const next = waitQueue.shift()!;
    if (next.aborted || next.signal?.aborted) {
      next.reject(abortError());
      continue;
    }
    grantImmediate(next.label);
    next.resolve();
    return;
  }
}

export function acquireGo2RtcSlot(options?: AcquireGo2RtcSlotOptions): Promise<void> {
  const signal = options?.signal;
  const label = (options?.label || '').trim();

  if (signal?.aborted) {
    queueLog(`cancelled camera=${label || '?'} reason=already-aborted`);
    return Promise.reject(abortError());
  }

  if (active < MAX_CONCURRENT) {
    queueLog(`queued camera=${label || '?'} (immediate)`);
    grantImmediate(label);
    return Promise.resolve();
  }

  return new Promise<void>((resolve, reject) => {
    const entry: WaitEntry = {
      label,
      signal,
      aborted: false,
      resolve,
      reject,
    };

    const onAbort = () => {
      if (entry.aborted) return;
      entry.aborted = true;
      const removed = removeFromQueue(entry);
      if (removed) {
        queueLog(
          `cancelled camera=${label || '?'} reason=unmounted queued=${waitQueue.length}`,
        );
      }
      // Always reject: safe if already settled; prevents hung promises if abort
      // races with promoteNext after the entry was shifted off the queue.
      reject(abortError());
    };

    waitQueue.push(entry);
    queueLog(`queued camera=${label || '?'} queued=${waitQueue.length} active=${active}`);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export function releaseGo2RtcSlot(label?: string): void {
  active = Math.max(0, active - 1);
  if (label) {
    queueLog(`slot-released camera=${label} active=${active} queued=${waitQueue.length}`);
  }
  promoteNext();
}

/** Dev / test helper — current queue depth (waiting, not yet started). */
export function getGo2RtcQueueSize(): number {
  return waitQueue.length;
}

/** Dev / test helper — slots currently held during connect. */
export function getGo2RtcActiveSlots(): number {
  return active;
}

/** Test helper */
export function resetGo2RtcConnectionLimiterForTests(): void {
  active = 0;
  waitQueue.length = 0;
}

if (isDev && typeof window !== 'undefined') {
  (window as unknown as {
    __go2rtcStreamQueue?: { size: () => number; active: () => number };
  }).__go2rtcStreamQueue = {
    size: getGo2RtcQueueSize,
    active: getGo2RtcActiveSlots,
  };
}
