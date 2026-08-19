import type { User } from '../services/authService';

export const PERMISSIONS = {
  LIVE_VIEW: 'Live View',
  RECORDING_VIEW: 'recording.view',
  PLAYBACK: 'Playback',
  EVENTS: 'Events',
  CAMERAS: 'Cameras',
  SYSTEM: 'System',
  USERS: 'Users',
} as const;

export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];

/** Operator-assignable operational permissions. Camera/user/platform pages are not assigned here. */
export const ALL_PERMISSIONS: Permission[] = [
  PERMISSIONS.LIVE_VIEW,
  PERMISSIONS.RECORDING_VIEW,
  PERMISSIONS.EVENTS,
  PERMISSIONS.SYSTEM,
];

export function permissionLabel(permission: string): string {
  if (permission === PERMISSIONS.RECORDING_VIEW) return 'View Recordings';
  return permission;
}

export function isAdminUser(user: Pick<User, 'role'> | null | undefined): boolean {
  return (user?.role ?? '').trim().toLowerCase() === 'admin';
}

/** Matches backend SUPER_ADMIN aliases. Do not treat this as Admin in the normal VMS UI. */
export function isSuperAdminUser(user: Pick<User, 'role'> | null | undefined): boolean {
  const key = (user?.role ?? '').trim().toLowerCase().replace(/[-\s]/g, '_');
  return key === 'super_admin' || key === 'superadmin';
}

/** Day-to-day CCTV admin: ADMIN or SUPER_ADMIN. Matches backend is_ops_admin / is_admin. */
export function isOpsAdminUser(user: Pick<User, 'role'> | null | undefined): boolean {
  return isAdminUser(user) || isSuperAdminUser(user);
}

export function isOperatorUser(user: Pick<User, 'role'> | null | undefined): boolean {
  return (user?.role ?? '').trim().toLowerCase() === 'operator';
}

export function isViewerUser(user: Pick<User, 'role'> | null | undefined): boolean {
  return (user?.role ?? '').trim().toLowerCase() === 'viewer';
}

export function hasPermission(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
  permission: Permission,
): boolean {
  if (!user) return false;
  if (isOpsAdminUser(user)) return true;
  // Operator/Viewer always get Live View + PTZ; camera ACL still limits which streams play.
  if (permission === PERMISSIONS.LIVE_VIEW && (isOperatorUser(user) || isViewerUser(user))) {
    return true;
  }
  return (user.permissions ?? []).includes(permission);
}

export function hasRecordingView(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
): boolean {
  return hasPermission(user, PERMISSIONS.RECORDING_VIEW);
}

/** Route path → required permission (exact or prefix match in canAccessPath). */
export const ROUTE_PERMISSIONS: Record<string, Permission> = {
  '/live': PERMISSIONS.LIVE_VIEW,
  '/playback': PERMISSIONS.RECORDING_VIEW,
  '/events': PERMISSIONS.EVENTS,
  '/ptz': PERMISSIONS.LIVE_VIEW,
  '/camera-management': PERMISSIONS.CAMERAS,
  '/storage': PERMISSIONS.SYSTEM,
  '/network-settings': PERMISSIONS.SYSTEM,
  '/user-management': PERMISSIONS.USERS,
  '/notifications': PERMISSIONS.SYSTEM,
  '/system-status': PERMISSIONS.SYSTEM,
  '/go2rtc-diagnostics': PERMISSIONS.SYSTEM,
  '/maintenance': PERMISSIONS.SYSTEM,
};

const ORDERED_PATHS = [
  '/live',
  '/playback',
  '/events',
  '/ptz',
  '/camera-management',
  '/storage',
  '/network-settings',
  '/user-management',
  '/notifications',
  '/system-status',
  '/go2rtc-diagnostics',
  '/maintenance',
] as const;

/** Platform / infrastructure pages — SUPER_ADMIN only. Never advertise these to ADMIN/OPERATOR. */
const SUPER_ADMIN_ONLY_PATHS = [
  '/storage',
  '/network-settings',
  '/system-status',
  '/go2rtc-diagnostics',
  '/maintenance',
] as const;

export function permissionForPath(pathname: string): Permission | null {
  if (pathname === '/' || pathname === '') {
    return PERMISSIONS.LIVE_VIEW;
  }
  const match = ORDERED_PATHS.find(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
  return match ? ROUTE_PERMISSIONS[match] : null;
}

function isControlCenterPath(pathname: string): boolean {
  return (
    pathname === '/control-center' ||
    pathname.startsWith('/control-center/') ||
    pathname === '/super-admin' ||
    pathname.startsWith('/super-admin/')
  );
}

function pathMatches(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

function isSuperAdminOnlyPath(pathname: string): boolean {
  if (isControlCenterPath(pathname)) return true;
  return SUPER_ADMIN_ONLY_PATHS.some((prefix) => pathMatches(pathname, prefix));
}

export function canAccessPath(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
  pathname: string,
): boolean {
  if (isSuperAdminOnlyPath(pathname)) {
    return isSuperAdminUser(user);
  }
  if (pathMatches(pathname, '/user-management')) {
    return isAdminUser(user);
  }
  if (pathMatches(pathname, '/camera-management')) {
    return isOpsAdminUser(user);
  }
  const required = permissionForPath(pathname);
  if (!required) return true;
  return hasPermission(user, required);
}

export function firstAllowedPath(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
): string | null {
  for (const path of ORDERED_PATHS) {
    if (!canAccessPath(user, path)) continue;
    return path;
  }
  return null;
}
