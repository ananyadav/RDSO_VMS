import React from 'react';
import { Redirect } from 'react-router-dom';
import type { User } from '../services/authService';
import type { Permission } from '../lib/permissions';
import { firstAllowedPath, hasPermission, isSuperAdminUser, permissionLabel } from '../lib/permissions';
import PermissionDenied from './PermissionDenied';

export function renderProtected(
  user: User,
  permission: Permission,
  children: React.ReactNode,
  area?: string,
): React.ReactNode {
  return hasPermission(user, permission) ? (
    children
  ) : (
    <PermissionDenied user={user} required={permission} area={area || permissionLabel(permission)} />
  );
}

/** Hidden platform route: silent redirect. Do not name SUPER_ADMIN in the UI. */
export function renderSuperAdminOnly(
  user: User,
  children: React.ReactNode,
  _area?: string,
): React.ReactNode {
  if (isSuperAdminUser(user)) return children;
  return <Redirect to={firstAllowedPath(user) || '/live'} />;
}

/** Hidden route: never advertise why access was denied. */
export function renderControlCenter(
  user: User,
  children: React.ReactNode,
): React.ReactNode {
  return renderSuperAdminOnly(user, children);
}

export function renderHome(
  user: User,
  children: React.ReactNode,
): React.ReactNode {
  if (hasPermission(user, 'Live View')) {
    return children;
  }
  const fallback = firstAllowedPath(user);
  if (fallback && fallback !== '/live') {
    return <Redirect to={fallback} />;
  }
  return <PermissionDenied user={user} area="Live View" required="Live View" />;
}
