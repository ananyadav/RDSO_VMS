import { apiFetch } from './api';

export interface PtzPreset {
  id: number;
  name: string;
  enabled?: boolean;
  token?: string;
}

export interface PtzTourStep {
  presetId: number;
  delay?: number;
  speed?: number;
  presetToken?: string;
}

export interface PtzTour {
  id: number;
  name: string;
  enabled?: boolean;
  token?: string;
  steps: PtzTourStep[];
}

/** Recorded PTZ pan/tilt/zoom path (RDSO 18.2.23) — distinct from tour/patrol. */
export interface PtzPattern {
  id: number;
  name: string;
  enabled?: boolean;
}

export interface PtzCapabilities {
  ok: boolean;
  supported?: boolean;
  backend?: string;
  presetsSupported?: boolean;
  toursSupported?: boolean;
  patternsSupported?: boolean;
  patternDistinctFromTourPatrol?: boolean;
  rdso_18_2_23?: boolean;
  presets?: {
    list?: boolean;
    set?: boolean;
    goto?: boolean;
    delete?: boolean;
  };
  tours?: {
    list?: boolean;
    set?: boolean;
    start?: boolean;
    stop?: boolean;
    delete?: boolean;
  };
  patterns?: {
    list?: boolean;
    set?: boolean;
    start?: boolean;
    stop?: boolean;
    record?: boolean;
    delete?: boolean;
  };
  error?: string;
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

export async function ptzStop(cameraId: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await apiFetch(`/api/ptz/${cameraId}/stop`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: data.error || `Stop failed (${res.status})` };
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : 'Stop failed' };
  }
}

export async function fetchPtzPresets(
  cameraId: string,
): Promise<{ ok: boolean; presets: PtzPreset[]; error?: string; supported?: boolean }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/presets`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return {
      ok: false,
      presets: [],
      supported: data.supported !== false,
      error: data.error || `Failed to load presets (${res.status})`,
    };
  }
  return { ok: true, presets: data.presets ?? [], supported: data.supported !== false };
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

export async function fetchPtzTours(
  cameraId: string,
): Promise<{ ok: boolean; tours: PtzTour[]; error?: string; supported?: boolean }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/tours`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return {
      ok: false,
      tours: [],
      supported: data.supported === true,
      error: data.error || `Failed to load tours (${res.status})`,
    };
  }
  return {
    ok: true,
    tours: data.tours ?? [],
    supported: data.supported !== false,
  };
}

export async function ptzSetTour(
  cameraId: string,
  tourId: number,
  payload: { name: string; steps: PtzTourStep[]; enabled?: boolean },
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/tours/${tourId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Save tour failed (${res.status})` };
  return { ok: true };
}

export async function ptzDeleteTour(
  cameraId: string,
  tourId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/tours/${tourId}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Delete tour failed (${res.status})` };
  return { ok: true };
}

export async function ptzStartTour(
  cameraId: string,
  tourId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/tours/${tourId}/start`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Start tour failed (${res.status})` };
  return { ok: true };
}

export async function ptzStopTour(
  cameraId: string,
  tourId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/tours/${tourId}/stop`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Stop tour failed (${res.status})` };
  return { ok: true };
}

export async function fetchPtzPatterns(
  cameraId: string,
): Promise<{ ok: boolean; patterns: PtzPattern[]; error?: string; supported?: boolean }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return {
      ok: false,
      patterns: [],
      supported: data.supported === true,
      error: data.error || `Failed to load patterns (${res.status})`,
    };
  }
  return {
    ok: true,
    patterns: data.patterns ?? [],
    supported: data.supported !== false,
  };
}

export async function ptzSetPattern(
  cameraId: string,
  patternId: number,
  name: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Save pattern failed (${res.status})` };
  return { ok: true };
}

export async function ptzDeletePattern(
  cameraId: string,
  patternId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Delete pattern failed (${res.status})` };
  return { ok: true };
}

export async function ptzStartPattern(
  cameraId: string,
  patternId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}/start`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Start pattern failed (${res.status})` };
  return { ok: true };
}

export async function ptzStopPattern(
  cameraId: string,
  patternId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}/stop`, { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Stop pattern failed (${res.status})` };
  return { ok: true };
}

export async function ptzRecordPatternStart(
  cameraId: string,
  patternId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}/record-start`, {
    method: 'POST',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Record start failed (${res.status})` };
  return { ok: true };
}

export async function ptzRecordPatternStop(
  cameraId: string,
  patternId: number,
): Promise<{ ok: boolean; error?: string }> {
  const res = await apiFetch(`/api/ptz/${cameraId}/patterns/${patternId}/record-stop`, {
    method: 'POST',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data.error || `Record stop failed (${res.status})` };
  return { ok: true };
}

export async function ptzCheckStatus(cameraId: string): Promise<PtzCapabilities> {
  const res = await apiFetch(`/api/ptz/${cameraId}/status`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok && data.ok == null) {
    return { ok: false, supported: false, error: data.error || `PTZ status failed (${res.status})` };
  }
  return data as PtzCapabilities;
}
