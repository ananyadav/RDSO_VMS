import { trackGo2RtcConsumer } from './liveProvider';

const activeUiConsumers = new Map<string, number>();

export function registerUiConsumer(stream: string): void {
  const prev = activeUiConsumers.get(stream) ?? 0;
  activeUiConsumers.set(stream, prev + 1);
  trackGo2RtcConsumer(stream, 1);
}

export function unregisterUiConsumer(stream: string): void {
  const prev = activeUiConsumers.get(stream) ?? 0;
  if (prev <= 1) {
    activeUiConsumers.delete(stream);
  } else {
    activeUiConsumers.set(stream, prev - 1);
  }
  trackGo2RtcConsumer(stream, -1);
}

/** Unregister all UI-tracked consumers (page unmount / refresh). */
export function flushAllUiConsumers(): void {
  for (const [stream, count] of [...activeUiConsumers.entries()]) {
    for (let i = 0; i < count; i++) {
      trackGo2RtcConsumer(stream, -1);
    }
  }
  activeUiConsumers.clear();
}
