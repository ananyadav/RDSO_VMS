/** Camera Management table row virtualization. */

export const MGMT_ROW_HEIGHT_PX = 54;
export const MGMT_OVERSCAN_ROWS = 3;

export function mgmtTableTotalHeight(count: number, rowHeightPx = MGMT_ROW_HEIGHT_PX): number {
  if (count <= 0) return 0;
  return count * rowHeightPx;
}

export function mgmtVisibleRowRange(
  count: number,
  scrollTop: number,
  viewportHeight: number,
  rowHeightPx = MGMT_ROW_HEIGHT_PX,
  overscan = MGMT_OVERSCAN_ROWS,
): { startIndex: number; endIndex: number; mountedCount: number; topPad: number; bottomPad: number } {
  if (count <= 0 || rowHeightPx <= 0) {
    return { startIndex: 0, endIndex: -1, mountedCount: 0, topPad: 0, bottomPad: 0 };
  }
  const startIndex = Math.max(0, Math.floor(scrollTop / rowHeightPx) - overscan);
  const endIndex = Math.min(
    count - 1,
    Math.ceil((scrollTop + Math.max(0, viewportHeight)) / rowHeightPx) + overscan,
  );
  const mountedCount = endIndex >= startIndex ? endIndex - startIndex + 1 : 0;
  const topPad = startIndex * rowHeightPx;
  const bottomPad = Math.max(0, mgmtTableTotalHeight(count, rowHeightPx) - (endIndex + 1) * rowHeightPx);
  return { startIndex, endIndex, mountedCount, topPad, bottomPad };
}

export function mgmtScrollTopForRowIndex(index: number, rowHeightPx = MGMT_ROW_HEIGHT_PX): number {
  return Math.max(0, index * rowHeightPx);
}
