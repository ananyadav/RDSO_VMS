/** Pure sequence rotation helpers for Live View playback (RDSO 18.1.11.2). */

export function advanceSequenceIndex(currentIndex: number, totalCameras: number): number {
  if (totalCameras <= 0) return 0;
  return (currentIndex + 1) % totalCameras;
}

/** Preserve backend order — never sort. */
export function sequenceCameraOrder(cameraIds: readonly string[]): string[] {
  return [...cameraIds];
}

export function sequencePositionLabel(index: number, total: number): string {
  if (total <= 0) return '0 / 0';
  return `${index + 1} / ${total}`;
}

export function shouldRotateSequence(totalAccessibleCameras: number): boolean {
  return totalAccessibleCameras > 1;
}

export function dwellMsFromSeconds(dwellSeconds: number): number {
  const n = Number(dwellSeconds);
  if (!Number.isFinite(n) || n <= 0) return 2000;
  return Math.floor(n * 1000);
}
