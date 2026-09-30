import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import {
  LIVE_MONITOR_IDS,
  MIN_LIVE_MONITORS,
  computeLiveGridRowHeight,
  liveDisplayMonitorControlCapabilityPublic,
  liveMonitorPath,
  liveMonitorStorageKey,
  loadLiveMonitorState,
  openLiveMonitorWindow,
  parseMonitorId,
  saveLiveMonitorState,
} from './liveMonitor';
import { assignCameraToSlot, buildDefaultAssignments } from './liveTileAssignments';
import { LIVE_LAYOUTS } from './liveLayouts';

describe('liveMonitor / RDSO 18.2.24–18.2.26 display control', () => {
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

  it('supports at least 8 monitor identities', () => {
    expect(MIN_LIVE_MONITORS).toBeGreaterThanOrEqual(8);
    expect(LIVE_MONITOR_IDS).toHaveLength(8);
  });

  it('parses and clamps monitor ids', () => {
    expect(parseMonitorId(null)).toBe(1);
    expect(parseMonitorId('3')).toBe(3);
    expect(parseMonitorId('99')).toBe(8);
  });

  it('persists independent layout/camera state per monitor (18.2.26)', () => {
    saveLiveMonitorState(1, {
      layoutLabel: '2x2',
      site: 'SiteA',
      building: 'B1',
      group: 'G1',
      slots: [{ kind: 'camera', id: 'cam-1' }, null],
    });
    saveLiveMonitorState(2, {
      layoutLabel: '4x4',
      site: 'SiteB',
      building: 'B2',
      group: 'G2',
      slots: buildDefaultAssignments(['cam-9'], 4),
    });
    const a = loadLiveMonitorState(1);
    const b = loadLiveMonitorState(2);
    expect(a?.layoutLabel).toBe('2x2');
    expect(a?.slots?.[0]).toEqual({ kind: 'camera', id: 'cam-1' });
    expect(b?.layoutLabel).toBe('4x4');
    expect(b?.slots).toHaveLength(16);
    expect(liveMonitorStorageKey(1)).not.toBe(liveMonitorStorageKey(2));
  });

  it('allows operator to change camera assignment per slot', () => {
    const slots = buildDefaultAssignments(['a', 'b', 'c', 'd'], 2);
    const next = assignCameraToSlot(slots, 3, 'a');
    expect(next[3]).toEqual({ kind: 'camera', id: 'a' });
    expect(next[0]).toBeNull();
  });

  it('exposes selectable layouts 1x1–6x6', () => {
    expect(LIVE_LAYOUTS.map((l) => l.id)).toEqual(['1x1', '2x2', '3x3', '4x4', '5x5', '6x6']);
  });

  it('sizes tiles from viewport — workstation and large/external (18.2.24)', () => {
    const workstation = computeLiveGridRowHeight(1920, 1080, 4);
    const lfdLike = computeLiveGridRowHeight(3840, 2160, 4);
    const small = computeLiveGridRowHeight(1280, 720, 2);
    expect(workstation).toBeGreaterThan(0);
    expect(lfdLike).toBeGreaterThan(workstation);
    expect(small).toBeGreaterThan(0);
    // Not a fixed hardcoded LFD/workstation pixel size
    expect(workstation).not.toBe(2160);
    expect(lfdLike).not.toBe(2160);
  });

  it('opens display windows from client PC (18.2.25)', () => {
    const opened: Array<{ url: string; name: string }> = [];
    const fakeWin = { opener: {} as Window | null };
    vi.stubGlobal('window', {
      open: (url: string, name: string) => {
        opened.push({ url, name });
        return fakeWin;
      },
    });
    expect(liveMonitorPath(3)).toBe('/live?monitor=3');
    const win = openLiveMonitorWindow(3);
    expect(win).toBe(fakeWin);
    expect(opened).toEqual([{ url: '/live?monitor=3', name: 'cctv-live-monitor-3' }]);
    expect(fakeWin.opener).toBeNull();
  });

  it('capability flags distinguish software support from physical 55" acceptance', () => {
    const cap = liveDisplayMonitorControlCapabilityPublic();
    expect(cap.rdso_18_2_24).toBe(true);
    expect(cap.rdso_18_2_25).toBe(true);
    expect(cap.rdso_18_2_26).toBe(true);
    expect(cap.hardcoded_physical_display_resolution).toBe(false);
    expect(cap.physical_55_inch_tested).toBe(false);
    expect(cap.client_pc_display_control).toBe(true);
    expect(cap.server_console_required).toBe(false);
    expect(cap.independent_monitor_layout_and_cameras).toBe(true);
    expect(cap.logical_monitors).toBe(8);
  });
});
