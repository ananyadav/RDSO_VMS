/** RDSO 18.6.17.3 — external VMS integration admin API. */

import { apiFetch, readJsonResponse } from './api';

export type VmsIntegration = {
  source_id: string;
  label: string;
  vendor_type: string;
  base_url: string;
  enabled: boolean;
  capabilities: {
    live?: boolean;
    playback?: boolean;
    events?: boolean;
    health?: boolean;
  };
  endpoints?: Record<string, string>;
  auth_type?: string;
  auth_header?: string;
  tls_verify?: boolean;
  timeout_seconds?: number;
  health?: string;
  last_seen?: string | null;
  last_success?: string | null;
  last_error?: string;
  has_credentials?: boolean;
  has_ingest_secret?: boolean;
  fake_vendor?: boolean;
  named_vendor_sdk?: boolean;
  adapter_kind?: string;
  integration_secret?: string;
  integration_secret_note?: string;
};

export async function fetchVmsIntegrations(): Promise<{ items: VmsIntegration[] }> {
  const res = await apiFetch('/api/ccc/admin/vms-integrations');
  return readJsonResponse(res);
}

export async function createVmsIntegration(body: {
  source_id: string;
  label?: string;
  base_url: string;
  credential?: string;
  enabled?: boolean;
  capabilities?: VmsIntegration['capabilities'];
  endpoints?: Record<string, string>;
  auth_type?: string;
  tls_verify?: boolean;
  timeout_seconds?: number;
}): Promise<{ integration: VmsIntegration }> {
  const res = await apiFetch('/api/ccc/admin/vms-integrations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return readJsonResponse(res);
}

export async function updateVmsIntegration(
  sourceId: string,
  patch: Partial<VmsIntegration> & { credential?: string },
): Promise<{ integration: VmsIntegration }> {
  const res = await apiFetch(`/api/ccc/admin/vms-integrations/${encodeURIComponent(sourceId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return readJsonResponse(res);
}

export async function testVmsIntegration(sourceId: string): Promise<{ ok: boolean; status?: string }> {
  const res = await apiFetch(
    `/api/ccc/admin/vms-integrations/${encodeURIComponent(sourceId)}/test`,
    { method: 'POST' },
  );
  return readJsonResponse(res);
}

export async function rotateVmsIntegrationSecret(
  sourceId: string,
): Promise<{ integration: VmsIntegration }> {
  const res = await apiFetch(
    `/api/ccc/admin/vms-integrations/${encodeURIComponent(sourceId)}/rotate-secret`,
    { method: 'POST' },
  );
  return readJsonResponse(res);
}

export async function deleteVmsIntegration(sourceId: string): Promise<void> {
  const res = await apiFetch(
    `/api/ccc/admin/vms-integrations/${encodeURIComponent(sourceId)}`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { error?: string }).error || 'Delete failed');
  }
}
