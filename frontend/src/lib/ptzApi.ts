import { apiFetch } from './api';

export interface PtzPreset {
  id: number;
  name: string;
  enabled?: boolean;
}

export interface PtzCamera {
  id: string;
  name: string;
  displayName?: string;
  online?: boolean;
  ip_address?: string;
  cameraUid?: string;
  workerId?: number | string | null;
  ptz?: boolean;
}

export async function fetchPtzCameras(): Promise<PtzCamera[]> {
  const res = await apiFetch('/api/ptz/cameras');
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Failed to load PTZ cameras (${res.status})`);
  }
  return data.cameras ?? [];
}

export async function ptzMove(
  cameraId: string,
  direction: string,
  speed: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction, speed }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Move failed (${res.status})` };
  return { ok: true };
}

export async function ptzStop(cameraId: string): Promise<void> {
  await apiFetch(`/api/ptz/${cameraId}/stop`, { method: 'POST' });
}

export async function fetchPtzPresets(cameraId: string): Promise<PtzPreset[]> {
  const res = await apiFetch(`/api/ptz/${cameraId}/presets`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.presets ?? [];
}

export async function ptzGotoPreset(cameraId: string, presetId: number): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/presets/${presetId}/goto`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Recall failed (${res.status})` };
  return { ok: true };
}

export async function ptzSetPreset(
  cameraId: string,
  presetId: number,
  name: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/presets/${presetId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Set preset failed (${res.status})` };
  return { ok: true };
}

export async function ptzDeletePreset(
  cameraId: string,
  presetId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/presets/${presetId}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Delete preset failed (${res.status})` };
  return { ok: true };
}

export async function ptzCheckStatus(cameraId: string): Promise<{ ok: boolean; supported?: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/status`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok && data.ok == null) {
    return { ok: false, supported: false, error: data.error || `PTZ status failed (${res.status})` };
  }
  return data;
}
