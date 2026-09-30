/** RDSO 18.6.22.18 — CCC historical reports client. */

import { apiFetch, readJsonResponse } from './api';

export type CccReportKind =
  | 'incidents'
  | 'events'
  | 'activity'
  | 'communications'
  | 'compliance';

export type CccReportPage = {
  report: string;
  items: Array<Record<string, unknown>>;
  total: number;
  limit: number;
  offset: number;
  returned?: number;
  empty?: boolean;
  app_timezone?: string;
  rdso_18_6_22_18?: boolean;
  [key: string]: unknown;
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

async function expectJson<T>(res: Response): Promise<T> {
  const data = await readJsonResponse<T & { error?: string }>(res);
  if (!res.ok) throw new Error(data?.error || res.statusText || 'Request failed');
  return data as T;
}

export async function fetchCccReport(
  kind: CccReportKind,
  params: Record<string, string | number | undefined>,
): Promise<CccReportPage> {
  const res = await apiFetch(`/api/ccc/reports/${kind}${qs(params)}`);
  return expectJson(res);
}

async function downloadBlob(res: Response, filename: string, mime: string): Promise<void> {
  if (!res.ok) {
    const data = await readJsonResponse<{ error?: string }>(res);
    throw new Error(data?.error || res.statusText || 'Export failed');
  }
  const text = await res.text();
  const blob = new Blob([text], { type: mime });
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

export async function downloadCccReportCsv(
  kind: CccReportKind,
  params: Record<string, string | number | undefined>,
  filename: string,
): Promise<void> {
  const res = await apiFetch(
    `/api/ccc/reports/${kind}${qs({ ...params, format: 'csv', limit: 2000, offset: 0 })}`,
  );
  await downloadBlob(res, filename, 'text/csv;charset=utf-8');
}

export async function downloadCccReportJson(
  kind: CccReportKind,
  params: Record<string, string | number | undefined>,
  filename: string,
): Promise<void> {
  const res = await apiFetch(
    `/api/ccc/reports/${kind}${qs({ ...params, format: 'json', limit: 2000, offset: 0 })}`,
  );
  await downloadBlob(res, filename, 'application/json;charset=utf-8');
}
