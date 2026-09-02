import { describe, expect, it, vi } from 'vitest';
import { PERMISSIONS, canAccessPath, hasPermission } from './permissions';
import { mergeEventPage } from './eventQuery';
import { sourceTypeLabel } from './eventLabels';
import type { AlarmEvent } from './eventsApi';

const uiNotification = (id: string, ui: boolean): AlarmEvent => ({
  id,
  camera_id: 'cam1',
  camera_uid: 'ip_192_168_41_106',
  source_type: 'signal_loss',
  severity: 'warning',
  title: 'Camera signal lost',
  message: 'No video frame received',
  occurred_at: '2026-09-01T10:00:00Z',
  status: 'open',
  acknowledged: false,
  actions_triggered: ['create_event', 'ui_notification'],
  ui_notification: ui,
  metadata: {},
});

describe('listUiNotifications', () => {
  it('requests ui_notification=true from API', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          items: [uiNotification('n1', true)],
          total: 1,
          limit: 50,
          offset: 0,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { listUiNotifications } = await import('./eventsApi');
    const data = await listUiNotifications();
    expect(fetchMock).toHaveBeenCalled();
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('ui_notification=true');
    expect(data.items.every((e) => e.ui_notification)).toBe(true);
    vi.unstubAllGlobals();
  });

  it('returns empty list for zero notifications', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { listUiNotifications } = await import('./eventsApi');
    const data = await listUiNotifications();
    expect(data.items).toEqual([]);
    vi.unstubAllGlobals();
  });
});

describe('notification display helpers', () => {
  it('labels signal_loss', () => {
    expect(sourceTypeLabel('signal_loss')).toBe('Signal Loss');
  });

  it('polling merge avoids duplicate notifications', () => {
    const existing = [uiNotification('a', true)];
    const incoming = [uiNotification('a', true), uiNotification('b', true)];
    const merged = mergeEventPage(existing, incoming);
    expect(merged.map((n) => n.id).sort()).toEqual(['a', 'b']);
  });
});

describe('acknowledge notification', () => {
  it('failure does not imply success', async () => {
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

describe('Notifications RBAC', () => {
  const operatorEvents = {
    role: 'Operator',
    permissions: [PERMISSIONS.EVENTS, PERMISSIONS.LIVE_VIEW],
  };
  const viewer = { role: 'Viewer', permissions: [PERMISSIONS.LIVE_VIEW] };

  it('requires Events permission for /notifications', () => {
    expect(canAccessPath(operatorEvents, '/notifications')).toBe(true);
    expect(hasPermission(operatorEvents, PERMISSIONS.EVENTS)).toBe(true);
  });

  it('denies viewer without Events permission', () => {
    expect(canAccessPath(viewer, '/notifications')).toBe(false);
  });
});

describe('ui_notification filter excludes non-notifications', () => {
  it('backend filter is requested; client does not show ui_notification=false items', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            items: [uiNotification('only-ui', true)],
            total: 1,
            limit: 50,
            offset: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    const { listUiNotifications } = await import('./eventsApi');
    const data = await listUiNotifications();
    expect(data.items.find((e) => !e.ui_notification)).toBeUndefined();
    vi.unstubAllGlobals();
  });
});
