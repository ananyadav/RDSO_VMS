import { apiFetch, readJsonResponse } from './api';
import type { Paginated } from './controlCenterApi';

export interface AlarmEvent {
  id: string;
  camera_id: string;
  camera_uid: string;
  rule_id?: string | null;
  source_type: string;
  severity: string;
  title: string;
  message: string;
  occurred_at: string;
  status: string;
  acknowledged: boolean;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
  actions_triggered: string[];
  ui_notification: boolean;
  metadata: Record<string, unknown>;
  recording_session_id?: string | null;
  recording_status?: string | null;
}

export class EventsRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'EventsRequestError';
    this.status = status;
  }
}

async function expectOk<T>(response: Response): Promise<T> {
  const data = await readJsonResponse<T & { error?: string }>(response);
  if (!response.ok) {
    throw new EventsRequestError(data?.error || response.statusText || 'Request failed', response.status);
  }
  return data as T;
}

export async function listEvents(params: URLSearchParams): Promise<Paginated<AlarmEvent>> {
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const response = await apiFetch(`/api/events${suffix}`);
  return expectOk<Paginated<AlarmEvent>>(response);
}

export async function listUiNotifications(options?: {
  acknowledged?: boolean;
  severity?: string;
  limit?: number;
  offset?: number;
}): Promise<Paginated<AlarmEvent>> {
  const q = new URLSearchParams();
  q.set('ui_notification', 'true');
  q.set('limit', String(options?.limit ?? 50));
  q.set('offset', String(options?.offset ?? 0));
  if (options?.severity) q.set('severity', options.severity);
  if (options?.acknowledged === true) q.set('acknowledged', 'true');
  if (options?.acknowledged === false) q.set('acknowledged', 'false');
  return listEvents(q);
}

export async function getEvent(id: string): Promise<AlarmEvent> {
  const response = await apiFetch(`/api/events/${id}`);
  return expectOk<AlarmEvent>(response);
}

export async function acknowledgeEvent(id: string): Promise<AlarmEvent> {
  const response = await apiFetch(`/api/events/${id}/acknowledge`, { method: 'POST' });
  return expectOk<AlarmEvent>(response);
}
