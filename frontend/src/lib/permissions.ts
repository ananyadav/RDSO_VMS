import type { User } from '../services/authService';

export const PERMISSIONS = {
  LIVE_VIEW: 'Live View',
  PLAYBACK: 'Playback',
  EVENTS: 'Events',
  CAMERAS: 'Cameras',
  SYSTEM: 'System',
  USERS: 'Users',
} as const;

export type Permission = (typeof PERMISSIONS)[keyof typeof PERMISSIONS];

export const ALL_PERMISSIONS: Permission[] = Object.values(PERMISSIONS);

export function isAdminUser(user: Pick<User, 'role'> | null | undefined): boolean {
  return (user?.role ?? '').trim().toLowerCase() === 'admin';
}

export function hasPermission(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
  permission: Permission,
): boolean {
  if (!user) return false;
  if (isAdminUser(user)) return true;
  return (user.permissions ?? []).includes(permission);
}

/** Route path → required permission (exact or prefix match in canAccessPath). */
export const ROUTE_PERMISSIONS: Record<string, Permission> = {
  '/live': PERMISSIONS.LIVE_VIEW,
  '/playback': PERMISSIONS.PLAYBACK,
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

export function permissionForPath(pathname: string): Permission | null {
  if (pathname === '/' || pathname === '') {
    return PERMISSIONS.LIVE_VIEW;
  }
  const match = ORDERED_PATHS.find(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
  return match ? ROUTE_PERMISSIONS[match] : null;
}

export function canAccessPath(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
  pathname: string,
): boolean {
  const required = permissionForPath(pathname);
  if (!required) return true;
  return hasPermission(user, required);
}

export function firstAllowedPath(
  user: Pick<User, 'role' | 'permissions'> | null | undefined,
): string | null {
  for (const path of ORDERED_PATHS) {
    const perm = ROUTE_PERMISSIONS[path];
    if (hasPermission(user, perm)) return path;
  }
  return null;
}
