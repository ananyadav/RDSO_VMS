import type { AlarmEvent } from './eventsApi';

export const DEFAULT_EVENT_PAGE_SIZE = 50;
export const EVENT_POLL_INTERVAL_MS = 8000;

export interface EventListFilters {
  camera_id: string;
  source_type: string;
  severity: string;
  status: string;
  acknowledged: '' | 'true' | 'false';
  from: string;
  to: string;
  offset: number;
  limit: number;
}

export function defaultEventFilters(): EventListFilters {
  return {
    camera_id: '',
    source_type: '',
    severity: '',
    status: '',
    acknowledged: '',
    from: '',
    to: '',
    offset: 0,
    limit: DEFAULT_EVENT_PAGE_SIZE,
  };
}

function dayStartIso(dateStr: string): string | undefined {
  if (!dateStr) return undefined;
  const d = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

function dayEndIso(dateStr: string): string | undefined {
  if (!dateStr) return undefined;
  const d = new Date(`${dateStr}T23:59:59.999`);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

export function buildEventQueryParams(filters: EventListFilters): URLSearchParams {
  const q = new URLSearchParams();
  q.set('limit', String(filters.limit));
  q.set('offset', String(filters.offset));
  if (filters.camera_id) q.set('camera_id', filters.camera_id);
  if (filters.source_type) q.set('source_type', filters.source_type);
  if (filters.severity) q.set('severity', filters.severity);
  if (filters.status) q.set('status', filters.status);
  if (filters.acknowledged === 'true' || filters.acknowledged === 'false') {
    q.set('acknowledged', filters.acknowledged);
  }
  const fromIso = dayStartIso(filters.from);
  const toIso = dayEndIso(filters.to);
  if (fromIso) q.set('from', fromIso);
  if (toIso) q.set('to', toIso);
  return q;
}

/** Merge polled items into current page without duplicate ids. */
export function mergeEventPage(existing: AlarmEvent[], incoming: AlarmEvent[]): AlarmEvent[] {
  const byId = new Map<string, AlarmEvent>();
  for (const ev of incoming) {
    byId.set(ev.id, ev);
  }
  for (const ev of existing) {
    if (!byId.has(ev.id)) {
      byId.set(ev.id, ev);
    }
  }
  return [...byId.values()].sort((a, b) => {
    const ta = a.occurred_at || '';
    const tb = b.occurred_at || '';
    return tb.localeCompare(ta);
  });
}

export function filtersKey(filters: EventListFilters): string {
  return JSON.stringify({
    camera_id: filters.camera_id,
    source_type: filters.source_type,
    severity: filters.severity,
    status: filters.status,
    acknowledged: filters.acknowledged,
    from: filters.from,
    to: filters.to,
    offset: filters.offset,
    limit: filters.limit,
  });
}
