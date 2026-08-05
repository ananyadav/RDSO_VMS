import { readJsonResponse } from '../lib/jsonResponse';

export interface CameraAccess {
  all?: boolean;
  allowedCameraGroups: string[];
  allowedCameraUids: string[];
  /** @deprecated legacy */
  accessType?: 'all' | 'group' | 'camera';
  allowedGroups?: string[];
  allowedCameraIds?: string[];
}

export interface User {
  id: string;
  name: string;
  role: string;
  lastLogin?: string;
  status?: string;
  email?: string;
  permissions?: string[];
  password?: string;
  cameraAccess?: CameraAccess;
}

const STORAGE_KEY = 'currentUser';
const SESSION_POLL_MS = 30_000;

type UnauthorizedHandler = (() => void) | null;

let onUnauthorized: UnauthorizedHandler = null;

export function setOnUnauthorized(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

function normalizeStoredUser(raw: User): User | null {
  if (!raw?.id) return null;
  return raw;
}

function persistUser(user: User): User {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  return user;
}

function endSession(): void {
  localStorage.removeItem(STORAGE_KEY);
  onUnauthorized?.();
}

export const authService = {
  async login(username: string, password: string): Promise<User> {
    const name = username.trim();
    const passTrimmed = password.trim();
    const res = await fetch('/api/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, password: passTrimmed }),
    });
    if (!res.ok) {
      const err = await readJsonResponse<{ error?: string }>(res).catch(() => ({} as { error?: string }));
      throw new Error(err.error || 'Invalid credentials');
    }
    const user = normalizeStoredUser(await readJsonResponse<User>(res));
    if (!user) {
      throw new Error('Login response missing user id — contact an administrator');
    }
    return persistUser(user);
  },

  getCurrentUser(): User | null {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    try {
      const user = normalizeStoredUser(JSON.parse(raw) as User);
      if (!user) {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      return user;
    } catch {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
  },

  getUserId(): string | null {
    return this.getCurrentUser()?.id ?? null;
  },

  async logout(): Promise<void> {
    try {
      await fetch('/api/logout', { method: 'POST', credentials: 'include' });
    } catch {
      // ignore network errors during logout
    }
    localStorage.removeItem(STORAGE_KEY);
  },

  handleUnauthorized(): void {
    if (!this.getCurrentUser()) return;
    endSession();
  },

  /** Validate session with server and refresh cached profile (permissions, role, etc.). */
  async refreshSession(): Promise<User | null> {
    try {
      const res = await fetch('/api/auth/session', { credentials: 'include' });
      if (res.status === 401) {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      if (!res.ok) {
        return this.getCurrentUser();
      }

      const contentType = res.headers.get('content-type') ?? '';
      if (!contentType.includes('application/json')) {
        console.warn('[auth] Session check returned non-JSON — is the backend running?');
        return this.getCurrentUser();
      }

      const fresh = normalizeStoredUser(await readJsonResponse<User>(res));
      if (!fresh) {
        this.handleUnauthorized();
        return null;
      }
      return persistUser(fresh);
    } catch (err) {
      console.warn('[auth] Session check failed:', err);
      return this.getCurrentUser();
    }
  },

  startSessionSync(onUserChange: (user: User | null) => void): () => void {
    let stopped = false;

    const sync = async () => {
      if (stopped) return;
      const next = await this.refreshSession();
      onUserChange(next);
    };

    setOnUnauthorized(() => {
      void this.logout();
      onUserChange(null);
    });

    const onFocus = () => {
      void sync();
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        void sync();
      }
    };

    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void sync();
      }
    }, SESSION_POLL_MS);

    return () => {
      stopped = true;
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
      window.clearInterval(interval);
      setOnUnauthorized(null);
    };
  },
};
