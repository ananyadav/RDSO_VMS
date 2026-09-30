import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LIVE_CONNECT_RAMP_DEFAULT,
  LIVE_DISPLAY_MIN_SIMULTANEOUS_TILES,
  LIVE_DISPLAY_SOFTWARE_FPS_CAPABILITY,
  LIVE_LAYOUTS,
  layoutById,
  layoutSlotCount,
  liveDisplayCapabilityPublic,
} from './liveLayouts';
import {
  buildDefaultAssignments,
  migrateAssignmentsForLayout,
  slotCountForLayout,
} from './liveTileAssignments';
import { go2rtcConnectRampLimit } from './go2rtcConnectionLimiter';
import { MIN_LIVE_MONITORS, saveLiveMonitorState, loadLiveMonitorState } from './liveMonitor';

describe('RDSO 18.2.5–18.2.7 live layouts', () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    store.clear();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('includes Full screen, Quad, 4x4 (16), and site layouts through 6x6', () => {
    expect(LIVE_LAYOUTS.map((l) => l.id)).toEqual(['1x1', '2x2', '3x3', '4x4', '5x5', '6x6']);
    expect(layoutById('1x1').rdsoName).toMatch(/Full screen/i);
    expect(layoutById('2x2').rdsoName).toMatch(/Quad/i);
    expect(layoutById('4x4').tileCount).toBe(16);
    expect(layoutById('6x6').tileCount).toBe(36);
  });

  it('4x4 provides 16 active slots even with fewer cameras', () => {
    expect(slotCountForLayout(0, 4)).toBe(16);
    expect(slotCountForLayout(3, 4)).toBe(16);
    expect(layoutSlotCount(4)).toBe(16);
    const slots = buildDefaultAssignments(['a', 'b', 'c'], 4);
    expect(slots).toHaveLength(16);
    expect(slots.filter(Boolean)).toHaveLength(3);
  });

  it('migrating to 4x4 keeps 16 slots', () => {
    const prev = buildDefaultAssignments(['a', 'b'], 2);
    const next = migrateAssignmentsForLayout(prev, 2, 4, new Set(['a', 'b']));
    expect(next).toHaveLength(16);
  });

  it('6x6 site layout has 36 slots', () => {
    expect(slotCountForLayout(10, 6)).toBe(36);
    expect(buildDefaultAssignments([], 6)).toHaveLength(36);
  });

  it('software supports ≥16 tiles and does not throttle FPS below 25', () => {
    expect(LIVE_DISPLAY_MIN_SIMULTANEOUS_TILES).toBe(16);
    expect(LIVE_DISPLAY_SOFTWARE_FPS_CAPABILITY).toBe(25);
    expect(LIVE_CONNECT_RAMP_DEFAULT).toBeGreaterThanOrEqual(16);
    expect(go2rtcConnectRampLimit()).toBeGreaterThanOrEqual(16);
    const cap = liveDisplayCapabilityPublic();
    expect(cap.software_fps_throttle_below_25).toBe(false);
    expect(cap.grid_uses_sub_stream).toBe(true);
    expect(cap.fullscreen_uses_main_stream).toBe(true);
    expect(cap.min_simultaneous_tiles).toBe(16);
  });

  it('supports independent monitor layout state (≥8)', () => {
    expect(MIN_LIVE_MONITORS).toBeGreaterThanOrEqual(8);
    saveLiveMonitorState(1, {
      layoutLabel: '4x4',
      site: 'A',
      building: null,
      group: 'g1',
      slots: buildDefaultAssignments(['cam1'], 4),
    });
    saveLiveMonitorState(2, {
      layoutLabel: '2x2',
      site: 'B',
      building: null,
      group: 'g2',
      slots: buildDefaultAssignments(['cam9'], 2),
    });
    expect(loadLiveMonitorState(1)?.layoutLabel).toBe('4x4');
    expect(loadLiveMonitorState(1)?.slots).toHaveLength(16);
    expect(loadLiveMonitorState(2)?.layoutLabel).toBe('2x2');
    expect(loadLiveMonitorState(2)?.slots).toHaveLength(4);
  });
});
