import { authService } from '../services/authService';

export function apiHeaders(extra?: HeadersInit): HeadersInit {
  const user = authService.getCurrentUser();
  const headers: Record<string, string> = {
    ...(extra as Record<string, string>),
  };
  if (user?.id) {
    headers['X-User-Id'] = user.id;
  }
  return headers;
}

export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const headers = apiHeaders(init?.headers);
  const response = await fetch(input, { ...init, headers });
  if (response.status === 401) {
    authService.handleUnauthorized();
  }
  return response;
}

export function cameraQuery(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}
