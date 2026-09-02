import { apiFetch, readJsonResponse } from './api';
import type { Paginated } from './controlCenterApi';

export interface AlarmRuleRuntime {
  last_triggered_at?: string | null;
  last_event_id?: string | null;
  trigger_count?: number;
}

export interface AlarmRule {
  id: string;
  name: string;
  enabled: boolean;
  camera_id: string;
  trigger: { source_type: string };
  actions: string[];
  severity: string;
  cooldown_seconds: number;
  recording?: { duration_seconds: number } | null;
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  runtime?: AlarmRuleRuntime;
}

export type AlarmRulePayload = {
  name: string;
  camera_id: string;
  trigger: { source_type: string };
  severity: string;
  actions: string[];
  cooldown_seconds: number;
  enabled: boolean;
  recording?: { duration_seconds: number };
};

export class AlarmRulesRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'AlarmRulesRequestError';
    this.status = status;
  }
}

async function expectOk<T>(response: Response): Promise<T> {
  const data = await readJsonResponse<T & { error?: string }>(response);
  if (!response.ok) {
    throw new AlarmRulesRequestError(data?.error || response.statusText || 'Request failed', response.status);
  }
  return data as T;
}

export async function listAlarmRules(params?: {
  camera_id?: string;
  enabled?: boolean;
  limit?: number;
  offset?: number;
}): Promise<Paginated<AlarmRule>> {
  const q = new URLSearchParams();
  if (params?.camera_id) q.set('camera_id', params.camera_id);
  if (params?.enabled !== undefined) q.set('enabled', params.enabled ? 'true' : 'false');
  if (params?.limit !== undefined) q.set('limit', String(params.limit));
  if (params?.offset !== undefined) q.set('offset', String(params.offset));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  const response = await apiFetch(`/api/alarm-rules${suffix}`);
  return expectOk<Paginated<AlarmRule>>(response);
}

export async function createAlarmRule(payload: AlarmRulePayload): Promise<AlarmRule> {
  const response = await apiFetch('/api/alarm-rules', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<AlarmRule>(response);
}

export async function updateAlarmRule(id: string, payload: Partial<AlarmRulePayload>): Promise<AlarmRule> {
  const response = await apiFetch(`/api/alarm-rules/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<AlarmRule>(response);
}

export async function deleteAlarmRule(id: string): Promise<void> {
  const response = await apiFetch(`/api/alarm-rules/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const data = await readJsonResponse<{ error?: string }>(response);
    throw new AlarmRulesRequestError(data?.error || 'Delete failed', response.status);
  }
}
