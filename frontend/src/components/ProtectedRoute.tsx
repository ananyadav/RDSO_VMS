import React from 'react';
import { Redirect } from 'react-router-dom';
import type { User } from '../services/authService';
import type { Permission } from '../lib/permissions';
import { firstAllowedPath, hasPermission } from '../lib/permissions';
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
    <PermissionDenied user={user} required={permission} area={area} />
  );
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
