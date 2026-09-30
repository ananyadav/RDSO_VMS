import { apiFetch, readJsonResponse } from './api';
import type { Paginated } from './controlCenterApi';

export type AlarmReportRow = {
  event_id: string;
  occurred_at?: string;
  camera_id: string;
  camera_uid: string;
  source_type: string;
  severity: string;
  status: string;
  acknowledged: boolean;
  alarm_state: string;
  title: string;
  recovered_at?: string;
  display_reset_at?: string;
};

export type IncidentReportRow = {
  event_id: string;
  occurred_at?: string;
  camera_id: string;
  camera_uid: string;
  title: string;
  message: string;
  source_type: string;
  severity: string;
  status: string;
  acknowledged: boolean;
  acknowledged_at?: string;
  alarm_state: string;
};

export type OperatorLogReportRow = {
  id: string;
  timestamp?: string;
  actor_user_id: string;
  actor_username: string;
  actor_role: string;
  action: string;
  resource_type: string;
  resource_id: string;
  resource_label: string;
  camera_id: string;
  success: boolean;
  status: string;
};

export type ReportPage<T> = Paginated<T> & { report: string };

export class ReportsRequestError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ReportsRequestError';
    this.status = status;
  }
}

function queryString(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue;
    q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

async function expectJson<T>(response: Response): Promise<T> {
  const data = await readJsonResponse<T & { error?: string }>(response);
  if (!response.ok) {
    throw new ReportsRequestError(data?.error || response.statusText || 'Request failed', response.status);
  }
  return data as T;
}

export async function fetchAlarmReport(params: {
  camera_id?: string;
  source_type?: string;
  severity?: string;
  status?: string;
  acknowledged?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<ReportPage<AlarmReportRow>> {
  const response = await apiFetch(`/api/reports/alarms${queryString(params)}`);
  return expectJson(response);
}

export async function fetchIncidentReport(params: {
  camera_id?: string;
  status?: string;
  severity?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<ReportPage<IncidentReportRow>> {
  const response = await apiFetch(`/api/reports/incidents${queryString(params)}`);
  return expectJson(response);
}

export async function fetchOperatorLogReport(params: {
  user?: string;
  action?: string;
  camera_id?: string;
  resource_id?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<ReportPage<OperatorLogReportRow>> {
  const response = await apiFetch(`/api/reports/operator-logs${queryString(params)}`);
  return expectJson(response);
}

/** Download CSV from report API (server builds + redacts). */
export async function downloadReportCsv(
  kind: 'alarms' | 'incidents' | 'operator-logs',
  params: Record<string, string | number | undefined>,
  filename: string,
): Promise<void> {
  const response = await apiFetch(
    `/api/reports/${kind}${queryString({ ...params, format: 'csv', limit: 2000, offset: 0 })}`,
  );
  if (!response.ok) {
    const data = await readJsonResponse<{ error?: string }>(response);
    throw new ReportsRequestError(data?.error || response.statusText || 'Export failed', response.status);
  }
  const text = await response.text();
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
