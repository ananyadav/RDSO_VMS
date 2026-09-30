import { describe, expect, it } from 'vitest';
import {
  PRIORITY_LEVELS,
  shouldAutoDisplayAlarm,
  sortAlarmsByPriority,
  priorityFromSeverity,
} from './priorityLevels';
import { selectAlarmForDisplay } from './alarmDisplay';
import type { AlarmEvent } from './eventsApi';

function ev(partial: Partial<AlarmEvent> & { id: string }): AlarmEvent {
  return {
    camera_id: 'c1',
    camera_uid: 'uid1',
    source_type: 'motion',
    severity: 'warning',
    title: 't',
    message: 'm',
    occurred_at: '2026-01-01T10:00:00Z',
    status: 'open',
    acknowledged: false,
    actions_triggered: ['ui_notification'],
    ui_notification: true,
    metadata: {},
    ...partial,
  };
}

describe('priorityLevels (RDSO 18.1.27)', () => {
  it('exposes five numeric levels', () => {
    expect(PRIORITY_LEVELS).toEqual([1, 2, 3, 4, 5]);
  });

  it('maps severity defaults without inventing roles', () => {
    expect(priorityFromSeverity('critical')).toBe(5);
    expect(priorityFromSeverity('info')).toBe(1);
  });

  it('orders by priority then severity then time', () => {
    const ordered = sortAlarmsByPriority([
      ev({ id: 'a', priority: 3, severity: 'critical', occurred_at: '2026-01-01T12:00:00Z' }),
      ev({ id: 'b', priority: 5, severity: 'info', occurred_at: '2026-01-01T11:00:00Z' }),
      ev({ id: 'c', priority: 5, severity: 'warning', occurred_at: '2026-01-01T13:00:00Z' }),
    ]);
    expect(ordered.map((e) => e.id)).toEqual(['c', 'b', 'a']);
  });

  it('applies user vs alarm conflict rule', () => {
    expect(shouldAutoDisplayAlarm(5, 3)).toBe(true);
    expect(shouldAutoDisplayAlarm(2, 4)).toBe(false);
    expect(shouldAutoDisplayAlarm(3, 3)).toBe(true);
  });

  it('selectAlarmForDisplay respects userPriority', () => {
    const events = [
      ev({ id: 'low', priority: 2, severity: 'info' }),
      ev({ id: 'high', priority: 5, severity: 'warning' }),
    ];
    const picked = selectAlarmForDisplay(events, { userPriority: 4 });
    expect(picked?.id).toBe('high');
    expect(selectAlarmForDisplay(events, { userPriority: 5 })?.id).toBe('high');
    expect(selectAlarmForDisplay([events[0]], { userPriority: 4 })).toBeNull();
  });
});
