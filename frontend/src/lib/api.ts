import { authService } from '../services/authService';

const SESSION_FETCH_INIT: RequestInit = { credentials: 'include' };

export function apiHeaders(extra?: HeadersInit): HeadersInit {
  return { ...(extra as Record<string, string>) };
}

/** Same-origin media/HLS — session cookie is sent automatically. */
export function withAuthQuery(url: string): string {
  return url;
}

export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = apiHeaders(init?.headers);
  const response = await fetch(input, { ...init, headers, credentials: 'include' });
  if (response.status === 401) {
    authService.handleUnauthorized();
  }
  return response;
}

export { readJsonResponse } from './jsonResponse';

export { SESSION_FETCH_INIT };

export function cameraQuery(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}
