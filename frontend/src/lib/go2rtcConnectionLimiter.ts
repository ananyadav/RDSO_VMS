/** Limit concurrent go2rtc *connect* attempts so cameras are not hammered all at once.

Slots are released after the first frame (or on failure) — they do NOT stay held
for the whole playback lifetime. Holding them caused the last camera in every
floor grid to wait behind the first N successful players.
*/

const MAX_CONCURRENT = Math.max(
  1,
  Number(import.meta.env.VITE_GO2RTC_MAX_CONCURRENT ?? 16),
);

let active = 0;
const waitQueue: Array<() => void> = [];

export function acquireGo2RtcSlot(): Promise<void> {
  if (active < MAX_CONCURRENT) {
    active += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    waitQueue.push(() => {
      active += 1;
      resolve();
    });
  });
}

export function releaseGo2RtcSlot(): void {
  active = Math.max(0, active - 1);
  const next = waitQueue.shift();
  if (next) next();
}

/** Test helper */
export function resetGo2RtcConnectionLimiterForTests(): void {
  active = 0;
  waitQueue.length = 0;
}
