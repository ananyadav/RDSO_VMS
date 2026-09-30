/** RDSO 18.6.5 / 18.6.6 / 18.6.15.4 / 18.6.16.1 — CCC dashboard / hot screen / comms API. */

import { apiFetch, readJsonResponse } from './api';

export type CccDashWidget = {
  id: string;
  label: string;
  enabled: boolean;
  order: number;
};

export type CccDashboard = {
  generated_at?: string;
  fake_statistics: boolean;
  cameras: {
    total: number;
    online: number | null;
    offline: number | null;
    online_status_known?: boolean;
    note?: string;
  };
  events: { open: number; critical: number };
  incidents: {
    open_active: number;
    escalated: number;
    critical_open: number;
    by_status?: Record<string, number>;
    by_priority?: Record<string, number>;
  };
  health: Record<string, unknown>;
  storage: Record<string, unknown>;
  recent: {
    incidents: Array<Record<string, unknown>>;
    events: Array<Record<string, unknown>>;
  };
  compliance: Record<string, unknown>;
  hot_screen_preview: CccHotItem[];
  prefs?: { user_id?: string; widgets: CccDashWidget[]; isolated?: boolean };
  poll_seconds_suggested?: number;
};

export type CccHotItem = {
  incident_id: string;
  title: string;
  location: string;
  severity: string;
  priority: number;
  status: string;
  assignee_group?: string | null;
  assignee_user_name?: string | null;
  elapsed_seconds?: number | null;
  camera_id?: string | null;
  camera_authorized?: boolean;
  live_href?: string | null;
  playback_href?: string | null;
  workspace_href?: string;
  via_vms_only?: boolean;
  direct_camera_rtsp?: boolean;
};

export type CccGroup = {
  id: string;
  name: string;
  kind: string;
  member_user_ids: string[];
  description?: string;
  not_rbac_role?: boolean;
};

export type CccMessageTemplate = {
  id: string;
  name: string;
  body: string;
  external_delivery: boolean;
  dmr_tetra: boolean;
  channel: string;
};

export type CccCommunication = {
  id: string;
  incident_id: string;
  subject?: string;
  body: string;
  recipient_user_id?: string | null;
  recipient_group?: string | null;
  status: string;
  sent_at?: string | null;
  delivered_at?: string | null;
  acknowledged_at?: string | null;
  external_delivery: boolean;
  dmr_tetra: boolean;
  channel: string;
};

export type CccCompliance = {
  sop_workflow_id?: string | null;
  total_steps: number;
  completed_steps: number;
  pending_steps: number;
  steps: Array<{ order?: number; title?: string; done?: boolean }>;
  assigned_user_id?: string | null;
  assigned_group?: string | null;
  due_at?: string | null;
  overdue: boolean;
  state: string;
};

export async function fetchCccDashboard(): Promise<CccDashboard> {
  const res = await apiFetch('/api/ccc/dashboard');
  return readJsonResponse(res);
}

export async function fetchCccDashboardPrefs(): Promise<{ widgets: CccDashWidget[]; user_id?: string }> {
  const res = await apiFetch('/api/ccc/dashboard/prefs');
  return readJsonResponse(res);
}

export async function saveCccDashboardPrefs(widgets: CccDashWidget[]): Promise<{ prefs: { widgets: CccDashWidget[] } }> {
  const res = await apiFetch('/api/ccc/dashboard/prefs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ widgets }),
  });
  return readJsonResponse(res);
}

export async function fetchCccHotScreen(limit = 20): Promise<{ items: CccHotItem[]; poll_seconds_suggested?: number }> {
  const res = await apiFetch(`/api/ccc/hot-screen?limit=${limit}`);
  return readJsonResponse(res);
}

export async function fetchCccGroups(kind?: string): Promise<{ items: CccGroup[] }> {
  const q = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  const res = await apiFetch(`/api/ccc/groups${q}`);
  return readJsonResponse(res);
}

export async function createCccGroup(body: {
  name: string;
  kind?: string;
  member_user_ids?: string[];
  description?: string;
}): Promise<{ group: CccGroup }> {
  const res = await apiFetch('/api/ccc/groups', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readJsonResponse(res);
}

export async function fetchCccMessageTemplates(): Promise<{ items: CccMessageTemplate[]; external_delivery: boolean }> {
  const res = await apiFetch('/api/ccc/message-templates');
  return readJsonResponse(res);
}

export async function createCccMessageTemplate(body: {
  name: string;
  body: string;
}): Promise<{ template: CccMessageTemplate }> {
  const res = await apiFetch('/api/ccc/message-templates', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readJsonResponse(res);
}

export async function sendCccCommunication(body: {
  incident_id: string;
  body?: string;
  subject?: string;
  template_id?: string;
  recipient_user_id?: string;
  recipient_group?: string;
}): Promise<{ communication: CccCommunication }> {
  const res = await apiFetch('/api/ccc/communications', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readJsonResponse(res);
}

export async function fetchCccCommunications(incidentId: string): Promise<{ items: CccCommunication[] }> {
  const res = await apiFetch(`/api/ccc/incidents/${encodeURIComponent(incidentId)}/communications`);
  return readJsonResponse(res);
}

export async function acknowledgeCccCommunication(id: string): Promise<{ communication: CccCommunication }> {
  const res = await apiFetch(`/api/ccc/communications/${encodeURIComponent(id)}/acknowledge`, {
    method: 'POST',
  });
  return readJsonResponse(res);
}

export async function fetchCccCompliance(incidentId: string): Promise<CccCompliance> {
  const res = await apiFetch(`/api/ccc/incidents/${encodeURIComponent(incidentId)}/compliance`);
  return readJsonResponse(res);
}

export function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${r}s`;
  return `${r}s`;
}
