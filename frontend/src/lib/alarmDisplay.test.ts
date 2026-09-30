/** RDSO 18.1.25 — alarm display selection / reset (pure). */

import { describe, expect, it } from 'vitest';
import type { AlarmEvent } from './eventsApi';
import {
  alarmDisplayBanner,
  isAlarmDisplayActive,
  selectAlarmForDisplay,
  shouldOpenAlarmDisplay,
} from './alarmDisplay';

function ev(partial: Partial<AlarmEvent> & Pick<AlarmEvent, 'id'>): AlarmEvent {
  return {
    camera_id: 'cam-1',
    camera_uid: 'uid-1',
    source_type: 'signal_loss',
    severity: 'warning',
    title: 'Signal loss',
    message: 'offline',
    occurred_at: '2026-09-08T10:00:00+00:00',
    status: 'open',
    acknowledged: false,
    actions_triggered: ['ui_notification'],
    ui_notification: true,
    metadata: {},
    ...partial,
  };
}

describe('alarmDisplay 18.1.25', () => {
  it('treats ui_notification open events as display-active', () => {
    expect(isAlarmDisplayActive(ev({ id: 'a' }))).toBe(true);
  });

  it('excludes acknowledged events from display', () => {
    expect(
      isAlarmDisplayActive(ev({ id: 'a', acknowledged: true, status: 'acknowledged' })),
    ).toBe(false);
  });

  it('excludes manually display-reset events without requiring ack', () => {
    const e = ev({
      id: 'a',
      acknowledged: false,
      metadata: { display_reset: true, display_reset_at: '2026-09-08T10:01:00+00:00' },
    });
    expect(isAlarmDisplayActive(e)).toBe(false);
    expect(e.acknowledged).toBe(false);
  });

  it('excludes auto-recovered signal-loss events from display', () => {
    expect(
      isAlarmDisplayActive(
        ev({
          id: 'a',
          metadata: { signal_restored: true, recovered_at: '2026-09-08T10:02:00+00:00' },
        }),
      ),
    ).toBe(false);
  });

  it('does not reopen the same event id once seen (dedupe / cooldown spam)', () => {
    const a = ev({ id: 'evt-1' });
    const seen = new Set<string>();
    expect(shouldOpenAlarmDisplay(a, seen)).toBe(true);
    seen.add(a.id);
    expect(shouldOpenAlarmDisplay(a, seen)).toBe(false);
  });

  it('keeps current alarm while still active; advances after recovery', () => {
    const a = ev({ id: 'a', severity: 'warning', occurred_at: '2026-09-08T10:00:00+00:00' });
    const b = ev({
      id: 'b',
      camera_id: 'cam-2',
      severity: 'critical',
      occurred_at: '2026-09-08T10:01:00+00:00',
    });
    expect(selectAlarmForDisplay([a, b], { currentEventId: 'a' })?.id).toBe('a');

    const aRecovered = {
      ...a,
      metadata: { signal_restored: true, recovered_at: '2026-09-08T10:03:00+00:00' },
    };
    expect(selectAlarmForDisplay([aRecovered, b], { currentEventId: 'a' })?.id).toBe('b');
  });

  it('prefers higher severity when no current display', () => {
    const info = ev({ id: 'i', severity: 'info' });
    const crit = ev({ id: 'c', severity: 'critical', camera_id: 'cam-2' });
    expect(selectAlarmForDisplay([info, crit])?.id).toBe('c');
  });

  it('skips locally dismissed ids so multiple alarms are not lost from queue', () => {
    const a = ev({ id: 'a' });
    const b = ev({ id: 'b', camera_id: 'cam-2', severity: 'critical' });
    const next = selectAlarmForDisplay([a, b], { locallyDismissedIds: ['a'] });
    expect(next?.id).toBe('b');
  });

  it('banner exposes camera, type, severity, time, and alarm state', () => {
    const banner = alarmDisplayBanner(ev({ id: 'x', severity: 'critical' }));
    expect(banner.cameraId).toBe('cam-1');
    expect(banner.sourceType).toBe('signal_loss');
    expect(banner.severity).toBe('critical');
    expect(banner.priority).toBe(5);
    expect(banner.occurredAt).toBeTruthy();
    expect(banner.state).toBe('alarmed');
  });

  it('banner state recovered vs reset vs acknowledged stay distinct', () => {
    expect(
      alarmDisplayBanner(
        ev({ id: '1', metadata: { signal_restored: true } }),
      ).state,
    ).toBe('recovered');
    expect(
      alarmDisplayBanner(
        ev({ id: '2', metadata: { display_reset: true } }),
      ).state,
    ).toBe('reset');
    expect(
      alarmDisplayBanner(
        ev({ id: '3', acknowledged: true, status: 'acknowledged' }),
      ).state,
    ).toBe('acknowledged');
  });
});
