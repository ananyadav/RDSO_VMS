import { describe, expect, it } from 'vitest';
import {
  POOL_DEFAULT_ITEM_HEIGHT,
  POOL_OVERSCAN_ITEMS,
  makeSyntheticPoolCameras,
  poolTotalHeight,
  poolVisibleIndexRange,
} from './liveCameraPoolVirtual';

describe('liveCameraPoolVirtual', () => {
  const itemH = POOL_DEFAULT_ITEM_HEIGHT;
  const viewport = 600;

  it('827 cameras at scroll top mounts only visible window', () => {
    const count = 827;
    const { mountedCount, startIndex, endIndex } = poolVisibleIndexRange(
      count,
      0,
      viewport,
      itemH,
      POOL_OVERSCAN_ITEMS,
    );
    expect(mountedCount).toBeLessThan(30);
    expect(mountedCount).toBeGreaterThan(0);
    expect(mountedCount).toBe(endIndex - startIndex + 1);
    expect(count - mountedCount).toBeGreaterThan(800);
  });

  it('1500 cameras at scroll top mounts only visible window', () => {
    const count = 1500;
    const { mountedCount } = poolVisibleIndexRange(count, 0, viewport, itemH, POOL_OVERSCAN_ITEMS);
    expect(mountedCount).toBeLessThan(30);
    expect(count - mountedCount).toBeGreaterThan(1470);
  });

  it('1500 cameras mid-scroll still mounts bounded window', () => {
    const count = 1500;
    const midScroll = poolTotalHeight(750, itemH);
    const { mountedCount, startIndex } = poolVisibleIndexRange(
      count,
      midScroll,
      viewport,
      itemH,
      POOL_OVERSCAN_ITEMS,
    );
    expect(mountedCount).toBeLessThan(30);
    expect(startIndex).toBeGreaterThan(700);
  });

  it('synthetic camera list length matches request', () => {
    expect(makeSyntheticPoolCameras(827)).toHaveLength(827);
    expect(makeSyntheticPoolCameras(1500)).toHaveLength(1500);
  });
});
