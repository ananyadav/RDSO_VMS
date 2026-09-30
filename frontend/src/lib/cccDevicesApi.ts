/** RDSO 18.6.22.3 / .12 / .15 — CCC devices / sensors / pre-emption API. */

import { apiFetch, readJsonResponse } from './api';

export type CccDevice = {
  id: string;
  device_uid: string;
  type: string;
  name: string;
  location: string;
  enabled: boolean;
  status: string;
  health: string;
  capabilities: string[];
  last_seen?: string | null;
  linked_camera_ids: string[];
  default_priority?: number;
  active_alert_count?: number;
  last_event_id?: string | null;
  has_integration_secret?: boolean;
  integration_secret?: string;
  opens_streams_on_register?: boolean;
};

export type CccPreemptionPolicy = {
  enabled: boolean;
  equal_priority: string;
  min_priority_to_preempt: number;
  scopes: Record<string, boolean>;
  distinct_from_rbac?: boolean;
  note?: string;
};

function qs(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '') continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

export async function fetchCccDevices(params: {
  type?: string;
  enabled?: string;
  status?: string;
  location?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<{ items: CccDevice[]; total: number; hard_count_cap?: boolean }> {
  const res = await apiFetch(`/api/ccc/devices${qs(params)}`);
  const data = await readJsonResponse<{ items?: CccDevice[]; total?: number; error?: string; hard_count_cap?: boolean }>(res);
  if (!res.ok) throw new Error(data.error || 'Failed to load devices');
  return { items: data.items || [], total: data.total || 0, hard_count_cap: data.hard_count_cap };
}

export async function createCccDevice(body: Record<string, unknown>): Promise<CccDevice> {
  const res = await apiFetch('/api/ccc/devices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ device?: CccDevice; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Create failed');
  return data.device as CccDevice;
}

export async function updateCccDevice(id: string, body: Record<string, unknown>): Promise<CccDevice> {
  const res = await apiFetch(`/api/ccc/devices/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ device?: CccDevice; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Update failed');
  return data.device as CccDevice;
}

export async function deleteCccDevice(id: string): Promise<void> {
  const res = await apiFetch(`/api/ccc/devices/${encodeURIComponent(id)}`, { method: 'DELETE' });
  const data = await readJsonResponse<{ error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Delete failed');
}

export async function fetchCccPreemptionPolicy(): Promise<CccPreemptionPolicy> {
  const res = await apiFetch('/api/ccc/admin/preemption-policy');
  return readJsonResponse(res);
}

export async function saveCccPreemptionPolicy(body: Record<string, unknown>): Promise<CccPreemptionPolicy> {
  const res = await apiFetch('/api/ccc/admin/preemption-policy', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await readJsonResponse<{ policy?: CccPreemptionPolicy; error?: string }>(res);
  if (!res.ok) throw new Error(data.error || 'Save failed');
  return data.policy as CccPreemptionPolicy;
}
