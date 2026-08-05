import { authService } from '../services/authService';
import { readJsonResponse } from './jsonResponse';

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

/** Retry transient proxy/backend failures (e.g. Atlas startup, port restart). */
export async function apiFetchWithRetry(
  input: string,
  init?: RequestInit,
  opts?: { retries?: number; retryStatuses?: number[]; delayMs?: number },
): Promise<Response> {
  const retries = opts?.retries ?? 4;
  const retryStatuses = opts?.retryStatuses ?? [502, 503];
  const delayMs = opts?.delayMs ?? 2500;
  let last: Response | null = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const res = await apiFetch(input, init);
    last = res;
    if (res.ok || !retryStatuses.includes(res.status) || attempt === retries) {
      return res;
    }
    await new Promise((resolve) => setTimeout(resolve, delayMs * (attempt + 1)));
  }
  return last!;
}

export async function apiErrorMessage(res: Response, fallback: string): Promise<string> {
  if (res.status === 401) {
    return 'Session expired — please log in again.';
  }
  if (res.status === 503 || res.status === 502) {
    return 'Backend is starting — wait a minute and refresh the page.';
  }
  try {
    const body = await readJsonResponse<{ error?: string }>(res);
    return body.error || `${fallback} (${res.status})`;
  } catch {
    return `${fallback} (${res.status})`;
  }
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
