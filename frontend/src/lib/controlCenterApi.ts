import { apiFetch, readJsonResponse } from './api';

export interface AuditLogItem {
  id: string;
  timestamp?: string;
  actor_user_id?: string | null;
  actor_username?: string | null;
  actor_role?: string | null;
  action?: string;
  resource_type?: string | null;
  resource_id?: string | null;
  resource_label?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  success?: boolean;
  status?: string | null;
  changes?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AuditLogQuery {
  user?: string;
  role?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  start?: string;
  end?: string;
  success?: '' | 'true' | 'false';
  limit?: number;
  offset?: number;
}

export interface SessionItem {
  id: string;
  user_id: string;
  user_name: string;
  role: string;
  created_at?: string | null;
  last_seen_at?: string | null;
  expires_at?: string | null;
  ip_address?: string | null;
  user_agent?: string | null;
  revoked?: boolean;
  revoked_at?: string | null;
  active?: boolean;
}

export interface ManagedUser {
  id: string;
  name: string;
  role: string;
  lastLogin?: string;
  status?: string;
  email?: string;
  permissions?: string[];
  cameraAccess?: {
    allowedCameraGroups?: string[];
    allowedCameraUids?: string[];
    accessType?: 'all';
    all?: boolean;
  };
}

export interface HealthStatus {
  ready?: boolean;
  mongodb?: boolean;
  cameraCount?: number;
  phase?: string;
}

export interface WorkerStatus {
  workerId?: number;
  pm2Name?: string;
  running?: boolean;
  assignedCameraCount?: number;
  liveStreamCount?: number;
  maxCameras?: number;
  apiPort?: number;
  rtspPort?: number;
  webrtcPort?: number;
}

export interface Go2RtcStatus {
  enabled?: boolean;
  running?: boolean;
  workersEnabled?: boolean;
  streamCount?: number;
  cameraCount?: number;
  binaryFound?: boolean;
  workers?: WorkerStatus[];
}

export class ControlCenterRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function safeMessage(status: number, body: { error?: string } | null): string {
  if (status === 401) return 'Your session ended. Please log in again.';
  if (status === 403) return 'This action is not permitted.';
  if (status === 404) return 'The requested record was not found.';
  const raw = (body?.error || '').trim();
  if (raw && !/mongo|traceback|stack|uri|password|token|exception|filepath|\\\\|\/home\/|\/var\/|C:\\/i.test(raw)) {
    if (raw.length < 160) return raw;
  }
  if (status >= 500) return 'The request could not be completed. Try again.';
  return 'The request could not be completed.';
}

async function parseBody(response: Response): Promise<{ error?: string } | null> {
  try {
    return await readJsonResponse<{ error?: string }>(response);
  } catch {
    return null;
  }
}

async function expectOk<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) return {} as T;
    return readJsonResponse<T>(response);
  }
  const body = await parseBody(response);
  throw new ControlCenterRequestError(response.status, safeMessage(response.status, body));
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

export async function fetchAuditLogs(query: AuditLogQuery): Promise<Paginated<AuditLogItem>> {
  const response = await apiFetch(
    `/api/audit-logs${queryString({
      user: query.user,
      role: query.role,
      action: query.action,
      resource_type: query.resource_type,
      resource_id: query.resource_id,
      start: query.start,
      end: query.end,
      success: query.success,
      limit: query.limit ?? 50,
      offset: query.offset ?? 0,
    })}`,
  );
  return expectOk<Paginated<AuditLogItem>>(response);
}

export async function fetchSessions(opts: {
  user_id?: string;
  active?: boolean;
  limit?: number;
  offset?: number;
}): Promise<Paginated<SessionItem>> {
  const response = await apiFetch(
    `/api/sessions${queryString({
      user: opts.user_id,
      active: opts.active ? 'true' : undefined,
      limit: opts.limit ?? 50,
      offset: opts.offset ?? 0,
    })}`,
  );
  return expectOk<Paginated<SessionItem>>(response);
}

export async function revokeUserSessions(userId: string): Promise<{ ok?: boolean; revoked?: number }> {
  const response = await apiFetch('/api/sessions/revoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  });
  return expectOk(response);
}

export async function fetchManagedUsers(): Promise<ManagedUser[]> {
  const response = await apiFetch('/api/users');
  return expectOk<ManagedUser[]>(response);
}

export async function createManagedUser(payload: Record<string, unknown>): Promise<ManagedUser> {
  const response = await apiFetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<ManagedUser>(response);
}

export async function updateManagedUser(id: string, payload: Record<string, unknown>): Promise<ManagedUser> {
  const response = await apiFetch(`/api/users/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return expectOk<ManagedUser>(response);
}

export async function deleteManagedUser(id: string): Promise<void> {
  const response = await apiFetch(`/api/users/${id}`, { method: 'DELETE' });
  await expectOk(response);
}

export async function fetchHealthStatus(): Promise<HealthStatus> {
  const response = await apiFetch('/api/health');
  return expectOk<HealthStatus>(response);
}

export async function fetchGo2RtcStatus(): Promise<Go2RtcStatus> {
  const response = await apiFetch('/api/go2rtc/status');
  return expectOk<Go2RtcStatus>(response);
}

export async function postGo2RtcAction(path: string): Promise<unknown> {
  const response = await apiFetch(path, { method: 'POST' });
  return expectOk(response);
}
