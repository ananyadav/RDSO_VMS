import React from 'react';
import { Link } from 'react-router-dom';
import { ShieldOff } from 'lucide-react';
import type { Permission } from '../lib/permissions';
import { firstAllowedPath } from '../lib/permissions';
import type { User } from '../services/authService';

interface PermissionDeniedProps {
  user: User;
  required?: Permission | null;
  area?: string;
}

export default function PermissionDenied({
  user,
  required,
  area,
}: PermissionDeniedProps): React.ReactElement {
  const fallback = firstAllowedPath(user);
  const label = area || required || 'this page';

  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[20rem] p-8 text-center">
      <ShieldOff size={48} className="text-gray-400 dark:text-gray-500 mb-4" strokeWidth={1.5} />
      <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
        You don&apos;t have permission
      </h2>
      <p className="text-gray-600 dark:text-gray-400 max-w-md mb-6">
        Your account is not allowed to access {label}.
        {required ? ` This area requires the "${required}" permission.` : ''}
        {' '}Contact an administrator if you need access.
      </p>
      {fallback ? (
        <Link
          to={fallback}
          className="btn-primary px-4 py-2 text-sm inline-flex items-center"
        >
          Go to {fallback === '/live' ? 'Live View' : 'your allowed page'}
        </Link>
      ) : (
        <p className="text-sm text-gray-500 dark:text-gray-500">
          No pages are assigned to your account yet.
        </p>
      )}
    </div>
  );
}
