/** Live camera pool list virtualization (matches LiveCameraGrid overscan style). */

export const POOL_GAP_PX = 4; // Tailwind space-y-1
export const POOL_OVERSCAN_ITEMS = 2;
/** Typical PoolItem height (label + IP + padding + border). */
export const POOL_DEFAULT_ITEM_HEIGHT = 46;

export function poolItemStride(itemHeightPx: number, gapPx = POOL_GAP_PX): number {
  return itemHeightPx + gapPx;
}

export function poolTotalHeight(
  count: number,
  itemHeightPx: number,
  gapPx = POOL_GAP_PX,
): number {
  if (count <= 0 || itemHeightPx <= 0) return 0;
  return count * itemHeightPx + Math.max(0, count - 1) * gapPx;
}

export function poolVisibleIndexRange(
  count: number,
  scrollTop: number,
  viewportHeight: number,
  itemHeightPx: number,
  overscan = POOL_OVERSCAN_ITEMS,
  gapPx = POOL_GAP_PX,
): { startIndex: number; endIndex: number; mountedCount: number } {
  if (count <= 0 || itemHeightPx <= 0) {
    return { startIndex: 0, endIndex: -1, mountedCount: 0 };
  }
  const stride = poolItemStride(itemHeightPx, gapPx);
  const startIndex = Math.max(0, Math.floor(scrollTop / stride) - overscan);
  const endIndex = Math.min(
    count - 1,
    Math.ceil((scrollTop + Math.max(0, viewportHeight)) / stride) + overscan,
  );
  const mountedCount = endIndex >= startIndex ? endIndex - startIndex + 1 : 0;
  return { startIndex, endIndex, mountedCount };
}

export function makeSyntheticPoolCameras(count: number): { id: string; name: string; online: boolean }[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `cam-${i + 1}`,
    name: `Camera ${i + 1}`,
    online: i % 3 !== 0,
  }));
}
