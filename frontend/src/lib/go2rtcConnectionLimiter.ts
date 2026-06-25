/** Limit concurrent go2rtc WebRTC/MSE players so cameras are not hammered all at once. */

const MAX_CONCURRENT = Number(import.meta.env.VITE_GO2RTC_MAX_CONCURRENT ?? 8);

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
