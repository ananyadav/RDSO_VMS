/** RDSO 18.1.25 / 18.1.27 — alarm display selection / reset helpers (pure). */

import type { AlarmEvent } from './eventsApi';
import {
  resolveAlarmPriority,
  shouldAutoDisplayAlarm,
  sortAlarmsByPriority,
} from './priorityLevels';

export function eventIsRecovered(event: AlarmEvent): boolean {
  const md = event.metadata || {};
  return Boolean(md.signal_restored || md.recovered_at);
}

export function eventIsDisplayReset(event: AlarmEvent): boolean {
  const md = event.metadata || {};
  return Boolean(md.display_reset_at || md.display_reset);
}

/** Active alarm display candidate — not ack'd, not manually reset, not auto-recovered. */
export function isAlarmDisplayActive(event: AlarmEvent): boolean {
  if (!event.ui_notification) return false;
  if (event.acknowledged) return false;
  if (event.status === 'acknowledged') return false;
  if (eventIsDisplayReset(event)) return false;
  if (eventIsRecovered(event)) return false;
  return true;
}

export function sortAlarmDisplayCandidates(events: AlarmEvent[]): AlarmEvent[] {
  return sortAlarmsByPriority(events);
}

/**
 * Pick which alarm should drive Live View display.
 * - Keeps current event if still active (no spam reopen of same id after dismiss).
 * - Skips locally dismissed ids.
 * - On recovery of current, advances to next active alarm.
 * - Optional userPriority: skip auto-candidates below operator priority (still in queue).
 */
export function selectAlarmForDisplay(
  events: AlarmEvent[],
  opts: {
    currentEventId?: string | null;
    locallyDismissedIds?: Set<string> | string[];
    userPriority?: number;
  } = {},
): AlarmEvent | null {
  const dismissed = new Set(
    opts.locallyDismissedIds instanceof Set
      ? opts.locallyDismissedIds
      : opts.locallyDismissedIds || [],
  );
  let active = sortAlarmDisplayCandidates(
    events.filter((e) => isAlarmDisplayActive(e) && !dismissed.has(e.id)),
  );
  if (opts.userPriority != null) {
    active = active.filter((e) =>
      shouldAutoDisplayAlarm(
        resolveAlarmPriority({ priority: e.priority, severity: e.severity }),
        opts.userPriority!,
      ),
    );
  }
  if (!active.length) return null;

  const currentId = opts.currentEventId || null;
  if (currentId) {
    const still = active.find((e) => e.id === currentId);
    if (still) return still;
  }
  return active[0];
}

/** True when a newly seen event id should open display (first sighting only). */
export function shouldOpenAlarmDisplay(
  event: AlarmEvent | null,
  seenEventIds: Set<string>,
): boolean {
  if (!event || !isAlarmDisplayActive(event)) return false;
  if (seenEventIds.has(event.id)) return false;
  return true;
}

export function alarmDisplayBanner(event: AlarmEvent): {
  cameraId: string;
  sourceType: string;
  severity: string;
  priority: number;
  title: string;
  occurredAt: string;
  state: 'alarmed' | 'recovered' | 'reset' | 'acknowledged';
} {
  let state: 'alarmed' | 'recovered' | 'reset' | 'acknowledged' = 'alarmed';
  if (event.acknowledged) state = 'acknowledged';
  else if (eventIsDisplayReset(event)) state = 'reset';
  else if (eventIsRecovered(event)) state = 'recovered';
  return {
    cameraId: event.camera_id,
    sourceType: event.source_type,
    severity: event.severity,
    priority: resolveAlarmPriority({ priority: event.priority, severity: event.severity }),
    title: event.title,
    occurredAt: event.occurred_at,
    state,
  };
}
