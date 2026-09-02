import { apiFetch, readJsonResponse } from './api';
import type { Paginated } from './controlCenterApi';

export interface CameraSequence {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  camera_ids: string[];
  dwell_seconds: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export type CameraSequencePayload = {
  name: string;
  description: string;
  enabled: boolean;
  camera_ids: string[];
  dwell_seconds: number;
};

export class CameraSequencesRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'CameraSequencesRequestError';
    this.status = status;
  }
}

async function expectOk<T>(response: Response): Promise<T> {
  const data = await readJsonResponse<T & { error?: string }>(response);
  if (!response.ok) {
    throw new CameraSequencesRequestError(
      data?.error || response.statusText || 'Request failed',
      response.status,
    );
  }
  return data as T;
}

export async function listCameraSequences(params?: {
  enabled?: boolean;
  limit?: number;
  offset?: number;
}): Promise<Paginated<CameraSequence>> {
  const q = new URLSearchParams();
  if (params?.enabled !== undefined) q.set('enabled', params.enabled ? 'true' : 'false');
  if (params?.limit !== undefined) q.set('limit', String(params.limit));
  if (params?.offset !== undefined) q.set('offset', String(params.offset));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  const response = await apiFetch(`/api/camera-sequences${suffix}`);
  return expectOk<Paginated<CameraSequence>>(response);
}

export async function getCameraSequence(id: string): Promise<CameraSequence> {
  const response = await apiFetch(`/api/camera-sequences/${id}`);
  return expectOk<CameraSequence>(response);
}

export async function createCameraSequence(payload: CameraSequencePayload): Promise<CameraSequence> {
  const response = await apiFetch('/api/camera-sequences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<CameraSequence>(response);
}

export async function updateCameraSequence(
  id: string,
  payload: Partial<CameraSequencePayload>,
): Promise<CameraSequence> {
  const response = await apiFetch(`/api/camera-sequences/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<CameraSequence>(response);
}

export async function deleteCameraSequence(id: string): Promise<void> {
  const response = await apiFetch(`/api/camera-sequences/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const data = await readJsonResponse<{ error?: string }>(response);
    throw new CameraSequencesRequestError(data?.error || 'Delete failed', response.status);
  }
}
