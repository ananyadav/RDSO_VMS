/** RDSO 18.6 — CCC incident / event-log / SOP client API. */

import { apiFetch, readJsonResponse } from './api';

export type CccIncident = {
  id: string;
  title: string;
  location: string;
  status: string;
  incident_time: string | null;
  severity: string;
  priority: number;
  critical?: boolean;
  assignee_user_id?: string | null;
  assignee_user_name?: string | null;
  assignee_group?: string | null;
  linked_event_ids: string[];
  linked_camera_ids: string[];
  notes: Array<{ at?: string; text?: string; author?: { name?: string } }>;
  timeline: Array<{
    at?: string;
    type?: string;
    message?: string;
    actor?: { name?: string; role?: string };
    detail?: Record<string, unknown>;
  }>;
  sop_workflow_id?: string | null;
  sop_step_index?: number;
  video?: {
    live_path_template?: string;
    playback_path_template?: string;
    via_vms_only?: boolean;
  };
};

export type CccEventLogRow = {
  event_id: string;
  event_time?: string;
  camera_id?: string;
  camera_uid?: string;
  location?: string;
  source_type?: string;
  severity?: string;
  priority?: number;
  title?: string;
  incident_id?: string;
  incident_status?: string;
  assignee_group?: string | null;
  assignee_user_name?: string | null;
  critical?: boolean;
  live_href?: string | null;
  playback_href?: string | null;
};

export type CccSopWorkflow = {
  id: string;
  name: string;
  description?: string;
  enabled: boolean;
  steps: Array<{ order: number; title: string; instruction?: string }>;
  escalation_rules: Array<Record<string, unknown>>;
  hot_reload?: boolean;
  requires_restart?: boolean;
};

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === '') return;
    sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function fetchCccEventLog(params: {
  limit?: number;
  offset?: number;
  severity?: string;
  status?: string;
  critical_only?: boolean;
  q?: string;
}): Promise<{ items: CccEventLogRow[]; total: number }> {
  const res = await apiFetch(`/api/ccc/event-log${qs(params)}`);
  return readJsonResponse(res);
}

export async function fetchCccIncidents(params: {
  limit?: number;
  offset?: number;
  status?: string;
  critical_only?: boolean;
  q?: string;
}): Promise<{ items: CccIncident[]; total: number }> {
  const res = await apiFetch(`/api/ccc/incidents${qs(params)}`);
  return readJsonResponse(res);
}

export async function fetchCccIncident(id: string): Promise<CccIncident> {
  const res = await apiFetch(`/api/ccc/incidents/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error('Incident not found');
  return readJsonResponse(res);
}

export async function createCccIncident(body: Record<string, unknown>): Promise<CccIncident> {
  const res = await apiFetch('/api/ccc/incidents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ ok?: boolean; incident?: CccIncident; error?: string; duplicate?: boolean }>(res);
  if (!res.ok) {
    const err = new Error(data.error || 'Create failed') as Error & { duplicate?: boolean };
    err.duplicate = Boolean(data.duplicate);
    throw err;
  }
  return data.incident as CccIncident;
}

export async function updateCccIncident(
  id: string,
  body: Record<string, unknown>,
): Promise<CccIncident> {
  const res = await apiFetch(`/api/ccc/incidents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ incident?: CccIncident; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Update failed');
  return data.incident as CccIncident;
}

export async function assignCccIncident(
  id: string,
  body: {
    assignee_group?: string;
    assignee_user_id?: string;
    assignee_user_name?: string;
    reason?: string;
  },
): Promise<CccIncident> {
  const res = await apiFetch(`/api/ccc/incidents/${encodeURIComponent(id)}/assign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ incident?: CccIncident; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Assign failed');
  return data.incident as CccIncident;
}

export async function processCccEscalations(): Promise<Record<string, unknown>> {
  const res = await apiFetch('/api/ccc/incidents/process-escalations', { method: 'POST' });
  return readJsonResponse(res);
}

export async function fetchCccSopWorkflows(): Promise<CccSopWorkflow[]> {
  const res = await apiFetch('/api/ccc/sop-workflows');
  const data = await readJsonResponse<{ items?: CccSopWorkflow[] }>(res);
  return data.items || [];
}

export async function createCccSopWorkflow(body: Record<string, unknown>): Promise<CccSopWorkflow> {
  const res = await apiFetch('/api/ccc/sop-workflows', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ workflow?: CccSopWorkflow; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'SOP create failed');
  return data.workflow as CccSopWorkflow;
}

export async function updateCccSopWorkflow(
  id: string,
  body: Record<string, unknown>,
): Promise<CccSopWorkflow> {
  const res = await apiFetch(`/api/ccc/sop-workflows/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ workflow?: CccSopWorkflow; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'SOP update failed');
  return data.workflow as CccSopWorkflow;
}

export function incidentLiveHref(cameraId: string): string {
  return `/live?camera=${encodeURIComponent(cameraId)}`;
}

export function incidentPlaybackHref(cameraId: string): string {
  return `/playback?camera=${encodeURIComponent(cameraId)}`;
}
