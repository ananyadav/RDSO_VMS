/** RDSO 18.2.28 — alarm-driven layout/slot switching (pure). */

import { describe, expect, it } from 'vitest';
import type { AlarmEvent } from './eventsApi';
import {
  applyAlarmDisplaySwitch,
  displaySwitchTargetsMonitor,
  parseAlarmDisplaySwitch,
  shouldUseLayoutSwitch,
  snapshotLiveViewState,
  alarmDisplaySwitchCapabilityPublic,
} from './alarmDisplaySwitch';
import { buildDefaultAssignments } from './liveTileAssignments';

function ev(partial: Partial<AlarmEvent> & Pick<AlarmEvent, 'id'>): AlarmEvent {
  return {
    camera_id: 'cam-1',
    camera_uid: 'uid-1',
    source_type: 'motion',
    severity: 'warning',
    title: 'Motion',
    message: 'motion',
    occurred_at: '2026-09-08T10:00:00+00:00',
    status: 'open',
    acknowledged: false,
    actions_triggered: ['ui_notification'],
    ui_notification: true,
    metadata: {},
    ...partial,
  };
}

describe('alarmDisplaySwitch 18.2.28', () => {
  it('falls back to fullscreen when no display_switch metadata', () => {
    expect(parseAlarmDisplaySwitch(ev({ id: 'a' }))).toBeNull();
    expect(shouldUseLayoutSwitch(null)).toBe(false);
  });

  it('parses monitor/layout/slot config', () => {
    const cfg = parseAlarmDisplaySwitch(
      ev({
        id: 'a',
        metadata: {
          display_switch: {
            mode: 'layout_switch',
            monitor_id: 3,
            layout: '4x4',
            slot: 5,
            restore_on_reset: true,
            camera_id: 'cam-1',
          },
        },
      }),
    );
    expect(cfg?.mode).toBe('layout_switch');
    expect(cfg?.monitorId).toBe(3);
    expect(cfg?.layout).toBe('4x4');
    expect(cfg?.slot).toBe(5);
    expect(shouldUseLayoutSwitch(cfg)).toBe(true);
  });

  it('targets only the configured monitor', () => {
    const cfg = parseAlarmDisplaySwitch(
      ev({
        id: 'a',
        metadata: {
          display_switch: { mode: 'layout_switch', monitor_id: 2, layout: '2x2', slot: 0 },
        },
      }),
    );
    expect(displaySwitchTargetsMonitor(cfg, 2)).toBe(true);
    expect(displaySwitchTargetsMonitor(cfg, 1)).toBe(false);
  });

  it('applies alarm camera into configured layout/slot', () => {
    const cfg = parseAlarmDisplaySwitch(
      ev({
        id: 'a',
        metadata: {
          display_switch: { mode: 'layout_switch', monitor_id: 1, layout: '2x2', slot: 2 },
        },
      }),
    )!;
    const prev = buildDefaultAssignments(['other'], 2);
    const next = applyAlarmDisplaySwitch({
      config: cfg,
      cameraId: 'cam-1',
      prevLayoutLabel: '2x2',
      prevSlots: prev,
      authorizedCameraIds: new Set(['cam-1', 'other']),
    });
    expect(next?.layoutLabel).toBe('2x2');
    expect(next?.slots[2]).toEqual({ kind: 'camera', id: 'cam-1' });
  });

  it('blocks unauthorized cameras', () => {
    const cfg = parseAlarmDisplaySwitch(
      ev({
        id: 'a',
        metadata: {
          display_switch: { mode: 'layout_switch', layout: '2x2', slot: 0 },
        },
      }),
    )!;
    expect(
      applyAlarmDisplaySwitch({
        config: cfg,
        cameraId: 'cam-denied',
        prevLayoutLabel: '2x2',
        prevSlots: buildDefaultAssignments([], 2),
        authorizedCameraIds: new Set(['cam-1']),
      }),
    ).toBeNull();
  });

  it('snapshots and restores previous monitor state', () => {
    const slots = buildDefaultAssignments(['a', 'b', 'c', 'd'], 2);
    const snap = snapshotLiveViewState('evt-1', '2x2', slots);
    expect(snap.eventId).toBe('evt-1');
    expect(snap.layoutLabel).toBe('2x2');
    expect(snap.slots[0]).toEqual({ kind: 'camera', id: 'a' });
    // Mutating original after snapshot must not mutate snapshot
    slots[0] = null;
    expect(snap.slots[0]).toEqual({ kind: 'camera', id: 'a' });
  });

  it('publishes capability flags', () => {
    const cap = alarmDisplaySwitchCapabilityPublic();
    expect(cap.rdso_18_2_28).toBe(true);
    expect(cap.fallback_fullscreen_without_config).toBe(true);
    expect(cap.unauthorized_cameras_blocked).toBe(true);
  });
});
