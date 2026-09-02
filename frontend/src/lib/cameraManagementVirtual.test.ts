import { describe, expect, it } from 'vitest';
import {
  MGMT_ROW_HEIGHT_PX,
  mgmtTableTotalHeight,
  mgmtVisibleRowRange,
} from './cameraManagementVirtual';

describe('cameraManagementVirtual', () => {
  const rowH = MGMT_ROW_HEIGHT_PX;
  const viewport = 640;

  it('827 cameras mounts bounded tbody rows', () => {
    const { mountedCount } = mgmtVisibleRowRange(827, 0, viewport, rowH);
    expect(mountedCount).toBeLessThan(25);
    expect(827 - mountedCount).toBeGreaterThan(800);
  });

  it('1500 cameras mounts bounded tbody rows', () => {
    const { mountedCount } = mgmtVisibleRowRange(1500, 0, viewport, rowH);
    expect(mountedCount).toBeLessThan(25);
    expect(1500 - mountedCount).toBeGreaterThan(1475);
  });

  it('padding spans preserve full scroll height', () => {
    const count = 1500;
    const { topPad, bottomPad, mountedCount } = mgmtVisibleRowRange(count, 0, viewport, rowH);
    const total = mgmtTableTotalHeight(count, rowH);
    expect(topPad + mountedCount * rowH + bottomPad).toBe(total);
  });
});
