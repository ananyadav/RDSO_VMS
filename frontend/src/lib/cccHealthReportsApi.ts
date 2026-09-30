/** RDSO 18.6.14 — CCC Health Reports API (non-GIS). */

import { apiFetch, readJsonResponse } from './api';

export type CccHealthRow = {
  component_type: string;
  component: string;
  status: string;
  last_check?: string;
  message?: string;
  location?: string;
  server_id?: string;
  detail?: string;
  password?: null;
  rtsp_url?: null;
};

export type CccHealthReport = {
  items: CccHealthRow[];
  total: number;
  limit: number;
  offset: number;
  returned: number;
  summary?: Record<string, number>;
  component_types?: string[];
  statuses?: string[];
  gis?: boolean;
  gis_pending?: boolean;
  fabricated_metrics?: boolean;
  streams_not_started?: boolean;
  rdso_18_6_14_health?: boolean;
  rdso_18_6_14_gis?: boolean;
  generated_at?: string;
};

export async function fetchCccHealthReport(opts?: {
  component_type?: string;
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<CccHealthReport> {
  const sp = new URLSearchParams();
  if (opts?.component_type) sp.set('component_type', opts.component_type);
  if (opts?.status) sp.set('status', opts.status);
  if (opts?.q) sp.set('q', opts.q);
  sp.set('limit', String(opts?.limit ?? 50));
  sp.set('offset', String(opts?.offset ?? 0));
  const res = await apiFetch(`/api/ccc/health-reports?${sp.toString()}`);
  return readJsonResponse(res);
}

export async function downloadCccHealthCsv(opts?: {
  component_type?: string;
  status?: string;
  q?: string;
}): Promise<void> {
  const sp = new URLSearchParams();
  sp.set('format', 'csv');
  if (opts?.component_type) sp.set('component_type', opts.component_type);
  if (opts?.status) sp.set('status', opts.status);
  if (opts?.q) sp.set('q', opts.q);
  sp.set('limit', '500');
  const res = await apiFetch(`/api/ccc/health-reports/export?${sp.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { error?: string }).error || 'Export failed');
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'ccc_health_report.csv';
  a.click();
  URL.revokeObjectURL(url);
}
