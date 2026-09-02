import { describe, expect, it, vi } from 'vitest';
import { PERMISSIONS, canAccessPath, hasPermission } from './permissions';
import { buildEventQueryParams, defaultEventFilters, mergeEventPage } from './eventQuery';
import { sourceTypeLabel } from './eventLabels';
import type { AlarmEvent } from './eventsApi';

const sampleEvent = (id: string, occurred_at: string): AlarmEvent => ({
  id,
  camera_id: 'cam1',
  camera_uid: 'ip_1',
  source_type: 'signal_loss',
  severity: 'warning',
  title: 'Camera signal lost',
  message: 'No video frame received',
  occurred_at,
  status: 'open',
  acknowledged: false,
  actions_triggered: ['create_event'],
  ui_notification: true,
  metadata: {},
});

describe('eventQuery', () => {
  it('builds backend filter query params', () => {
    const q = buildEventQueryParams({
      ...defaultEventFilters(),
      camera_id: '507f1f77bcf86cd799439011',
      source_type: 'signal_loss',
      severity: 'warning',
      status: 'open',
      acknowledged: 'false',
      from: '2026-09-01',
      to: '2026-09-01',
      offset: 50,
      limit: 50,
    });
    expect(q.get('camera_id')).toBe('507f1f77bcf86cd799439011');
    expect(q.get('source_type')).toBe('signal_loss');
    expect(q.get('severity')).toBe('warning');
    expect(q.get('status')).toBe('open');
    expect(q.get('acknowledged')).toBe('false');
    expect(q.get('from')).toBeTruthy();
    expect(q.get('to')).toBeTruthy();
    expect(q.get('limit')).toBe('50');
    expect(q.get('offset')).toBe('50');
  });

  it('mergeEventPage avoids duplicate ids when polling', () => {
    const existing = [sampleEvent('a', '2026-09-01T12:00:00Z')];
    const incoming = [
      sampleEvent('a', '2026-09-01T12:00:00Z'),
      sampleEvent('b', '2026-09-01T13:00:00Z'),
    ];
    const merged = mergeEventPage(existing, incoming);
    expect(merged.map((e) => e.id)).toEqual(['b', 'a']);
  });
});

describe('eventLabels', () => {
  it('displays signal_loss label', () => {
    expect(sourceTypeLabel('signal_loss')).toBe('Signal Loss');
  });
});

describe('eventsApi', () => {
  it('loads events from GET /api/events', async () => {
    const payload = {
      items: [sampleEvent('e1', '2026-09-01T10:00:00Z')],
      total: 1,
      limit: 50,
      offset: 0,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { listEvents } = await import('./eventsApi');
    const data = await listEvents(new URLSearchParams({ limit: '50' }));
    expect(data.items).toHaveLength(1);
    expect(data.items[0].source_type).toBe('signal_loss');
    vi.unstubAllGlobals();
  });

  it('acknowledge failure throws and does not imply success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: 'Event not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { acknowledgeEvent, EventsRequestError } = await import('./eventsApi');
    await expect(acknowledgeEvent('missing')).rejects.toBeInstanceOf(EventsRequestError);
    vi.unstubAllGlobals();
  });
});

describe('Events RBAC', () => {
  const admin = { role: 'Admin', permissions: [] as string[] };
  const operatorWithEvents = {
    role: 'Operator',
    permissions: [PERMISSIONS.EVENTS, PERMISSIONS.LIVE_VIEW],
  };
  const viewer = { role: 'Viewer', permissions: [PERMISSIONS.LIVE_VIEW] };

  it('allows Events permission holders to access /events route', () => {
    expect(hasPermission(admin, PERMISSIONS.EVENTS)).toBe(true);
    expect(hasPermission(operatorWithEvents, PERMISSIONS.EVENTS)).toBe(true);
    expect(canAccessPath(operatorWithEvents, '/events')).toBe(true);
  });

  it('denies viewer without Events permission', () => {
    expect(hasPermission(viewer, PERMISSIONS.EVENTS)).toBe(false);
    expect(canAccessPath(viewer, '/events')).toBe(false);
  });
});

describe('empty events response', () => {
  it('returns zero items for empty backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { listEvents } = await import('./eventsApi');
    const data = await listEvents(new URLSearchParams());
    expect(data.items).toEqual([]);
    expect(data.total).toBe(0);
    vi.unstubAllGlobals();
  });
});
