/** RDSO 18.1.27 — numeric priority (1–5, 5 highest). Distinct from RBAC roles. */

export const PRIORITY_MIN = 1;
export const PRIORITY_MAX = 5;
export const PRIORITY_LEVELS = [1, 2, 3, 4, 5] as const;
export const DEFAULT_USER_PRIORITY = 1;
export const DEFAULT_ALARM_PRIORITY = 3;

export const SEVERITY_DEFAULT_PRIORITY: Record<string, number> = {
  critical: 5,
  warning: 3,
  info: 1,
};

export const SEVERITY_RANK: Record<string, number> = {
  critical: 3,
  warning: 2,
  info: 1,
};

export const PRIORITY_OPTIONS = PRIORITY_LEVELS.map((value) => ({
  value,
  label: `P${value}${value === PRIORITY_MAX ? ' (highest)' : value === PRIORITY_MIN ? ' (lowest)' : ''}`,
}));

export function normalizePriority(
  raw: unknown,
  defaultValue: number = DEFAULT_ALARM_PRIORITY,
): number {
  const n = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isFinite(n)) return defaultValue;
  const v = Math.floor(n);
  if (v < PRIORITY_MIN || v > PRIORITY_MAX) return defaultValue;
  return v;
}

export function priorityFromSeverity(severity: string | null | undefined): number {
  const key = String(severity || '').toLowerCase();
  return SEVERITY_DEFAULT_PRIORITY[key] ?? DEFAULT_ALARM_PRIORITY;
}

export function resolveAlarmPriority(opts: {
  priority?: unknown;
  severity?: string | null;
}): number {
  if (opts.priority === undefined || opts.priority === null || opts.priority === '') {
    return priorityFromSeverity(opts.severity);
  }
  return normalizePriority(opts.priority, priorityFromSeverity(opts.severity));
}

/** Auto-fullscreen when alarm.priority >= user.priority (equal → alarm wins). */
export function shouldAutoDisplayAlarm(
  alarmPriority: number,
  userPriority: number,
): boolean {
  return normalizePriority(alarmPriority) >= normalizePriority(userPriority, DEFAULT_USER_PRIORITY);
}

export function sortAlarmsByPriority<T extends { priority?: number; severity?: string; occurred_at?: string }>(
  events: T[],
): T[] {
  return [...events].sort((a, b) => {
    const pa = resolveAlarmPriority({ priority: a.priority, severity: a.severity });
    const pb = resolveAlarmPriority({ priority: b.priority, severity: b.severity });
    if (pb !== pa) return pb - pa;
    const sa = SEVERITY_RANK[String(a.severity || '').toLowerCase()] || 0;
    const sb = SEVERITY_RANK[String(b.severity || '').toLowerCase()] || 0;
    if (sb !== sa) return sb - sa;
    return String(b.occurred_at || '').localeCompare(String(a.occurred_at || ''));
  });
}
