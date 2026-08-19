/** SUPER_ADMIN Control Center display helpers. Do not use in ADMIN/OPERATOR UI. */

const SUPER_ADMIN_KEYS = new Set(['super_admin', 'superadmin']);

export function normalizeRoleKey(role: string | null | undefined): string {
  return (role ?? '').trim().toLowerCase().replace(/[-\s]/g, '_');
}

export function isSuperAdminRole(role: string | null | undefined): boolean {
  const key = normalizeRoleKey(role);
  return SUPER_ADMIN_KEYS.has(key);
}

export function displayRole(role: string | null | undefined): string {
  const key = normalizeRoleKey(role);
  if (SUPER_ADMIN_KEYS.has(key)) return 'Super Admin';
  if (key === 'admin' || key === 'administrator') return 'Admin';
  if (key === 'operator') return 'Operator';
  if (key === 'viewer') return 'Viewer';
  return (role || '').trim() || '—';
}

export function formatLocalDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function localDayStartIso(yyyyMmDd: string): string {
  return new Date(`${yyyyMmDd}T00:00:00`).toISOString();
}

export function localDayEndIso(yyyyMmDd: string): string {
  return new Date(`${yyyyMmDd}T23:59:59.999`).toISOString();
}

export function summarizeUserAgent(ua: string | null | undefined): string {
  if (!ua) return '—';
  const browser = ua.includes('Edg/')
    ? 'Edge'
    : ua.includes('Chrome/')
      ? 'Chrome'
      : ua.includes('Firefox/')
        ? 'Firefox'
        : ua.includes('Safari/')
          ? 'Safari'
          : 'Browser';
  const os = ua.includes('Windows')
    ? 'Windows'
    : ua.includes('Android')
      ? 'Android'
      : ua.includes('iPhone') || ua.includes('iPad')
        ? 'iOS'
        : ua.includes('Mac OS') || ua.includes('Macintosh')
          ? 'macOS'
          : ua.includes('Linux')
            ? 'Linux'
            : 'Unknown';
  return `${browser} · ${os}`;
}

const FIELD_LABELS: Record<string, string> = {
  ip_address: 'Camera IP',
  name: 'Name',
  protocol: 'Protocol',
  site: 'Site',
  building: 'Building',
  floor: 'Floor',
  camera_group: 'Location',
  location_path: 'Location path',
  username: 'Username',
  port: 'Port',
  is_active: 'Active',
  role: 'Role',
  status: 'Status',
  email: 'Email',
  main_rtsp_url: 'Main stream URL',
  sub_rtsp_url: 'Sub stream URL',
  recording_rtsp_url: 'Recording stream URL',
  camera_password: 'Password',
  password_changed: 'Password',
  permissions: 'Permissions',
};

export function humanizeField(field: string): string {
  if (FIELD_LABELS[field]) return FIELD_LABELS[field];
  return field
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

export function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (value === true) return 'Yes';
  if (value === false) return 'No';
  if (Array.isArray(value)) return value.length ? value.map(String).join(', ') : '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return '—';
    }
  }
  return String(value);
}

const LOGIN_REASON_LABELS: Record<string, string> = {
  invalid_credentials: 'Invalid credentials',
  account_disabled: 'Account disabled',
  missing_credentials: 'Missing credentials',
};

export function loginFailureLabel(metadata: Record<string, unknown> | null | undefined): string | null {
  const reason = metadata?.internal_reason;
  if (typeof reason !== 'string') return null;
  return LOGIN_REASON_LABELS[reason] ?? null;
}

export function actorDisplayName(item: {
  actor_username?: string | null;
  resource_label?: string | null;
}): string {
  return item.actor_username || item.resource_label || '—';
}
