import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  type AuditLogItem,
  type AuditLogQuery,
  ControlCenterRequestError,
  fetchAuditLogs,
} from '../../lib/controlCenterApi';
import {
  actorDisplayName,
  displayRole,
  formatLocalDateTime,
  localDayEndIso,
  localDayStartIso,
  loginFailureLabel,
  summarizeUserAgent,
} from '../../lib/superAdmin';
import AuditDetailModal from './AuditDetailModal';

export const AUDIT_ACTIONS = [
  'LOGIN_SUCCESS',
  'LOGIN_FAILED',
  'LOGOUT',
  'USER_CREATED',
  'USER_UPDATED',
  'USER_DISABLED',
  'USER_ENABLED',
  'USER_PASSWORD_RESET',
  'USER_ROLE_CHANGED',
  'USER_DELETED',
  'CAMERA_CREATED',
  'CAMERA_UPDATED',
  'CAMERA_DELETED',
  'CAMERA_LOCATION_CHANGED',
  'LOCATION_CREATED',
  'LOCATION_UPDATED',
  'LOCATION_DELETED',
  'PTZ_PAN',
  'PTZ_TILT',
  'PTZ_ZOOM',
  'PTZ_STOP',
  'SESSION_REVOKED',
] as const;

const PAGE_SIZE = 50;

export interface AuditTableUserOption {
  id: string;
  name: string;
}

export interface AuditLockedFilters {
  user?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
}

interface AuditLogTableProps {
  users?: AuditTableUserOption[];
  locked?: AuditLockedFilters;
  extraActions?: string[];
  actionsOnly?: string[];
  emptyMessage?: string;
}

export default function AuditLogTable({
  users = [],
  locked,
  extraActions,
  actionsOnly,
  emptyMessage = 'No audit events for these filters.',
}: AuditLogTableProps): React.ReactElement {
  const [user, setUser] = useState(locked?.user || '');
  const [role, setRole] = useState('');
  const [action, setAction] = useState(locked?.action || '');
  const [resourceType, setResourceType] = useState(locked?.resource_type || '');
  const [resourceId, setResourceId] = useState(locked?.resource_id || '');
  const [success, setSuccess] = useState<'' | 'true' | 'false'>('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AuditLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AuditLogItem | null>(null);

  useEffect(() => {
    setUser(locked?.user || '');
    setAction(locked?.action || '');
    setResourceType(locked?.resource_type || '');
    setResourceId(locked?.resource_id || '');
    setOffset(0);
  }, [locked?.user, locked?.action, locked?.resource_type, locked?.resource_id]);

  const query: AuditLogQuery = useMemo(
    () => ({
      user: locked?.user || user || undefined,
      role: role || undefined,
      action: locked?.action || action || undefined,
      resource_type: (() => {
        const type = locked?.resource_type || resourceType || undefined;
        const act = locked?.action || action || undefined;
        if (act === 'CAMERA_LOCATION_CHANGED' && type === 'location') return undefined;
        return type;
      })(),
      resource_id: locked?.resource_id || resourceId || undefined,
      success,
      start: start ? localDayStartIso(start) : undefined,
      end: end ? localDayEndIso(end) : undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [action, end, locked, offset, resourceId, resourceType, role, start, success, user],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAuditLogs(query);
      setItems(data.items || []);
      setTotal(data.total || 0);
      setLimit(data.limit || PAGE_SIZE);
    } catch (err) {
      const message = err instanceof ControlCenterRequestError ? err.message : 'Could not load audit logs.';
      setError(message);
      setItems([]);
      setTotal(0);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const actions = useMemo(() => {
    if (actionsOnly?.length) return actionsOnly;
    const extra = extraActions || [];
    return Array.from(new Set([...AUDIT_ACTIONS, ...extra]));
  }, [actionsOnly, extraActions]);

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2">
        <select
          className="select-style"
          value={locked?.user ? locked.user : user}
          disabled={Boolean(locked?.user)}
          onChange={(e) => {
            setUser(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All users</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </select>
        <select className="select-style" value={role} onChange={(e) => { setRole(e.target.value); setOffset(0); }}>
          <option value="">All roles</option>
          <option value="SUPER_ADMIN">Super Admin</option>
          <option value="Admin">Admin</option>
          <option value="Operator">Operator</option>
          <option value="Viewer">Viewer</option>
        </select>
        <select
          className="select-style"
          value={locked?.action ? locked.action : action}
          disabled={Boolean(locked?.action)}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All actions</option>
          {actions.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <select
          className="select-style"
          value={locked?.resource_type ? locked.resource_type : resourceType}
          disabled={Boolean(locked?.resource_type)}
          onChange={(e) => {
            setResourceType(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All resources</option>
          <option value="camera">Camera</option>
          <option value="location">Location</option>
          <option value="user">User</option>
          <option value="auth">Auth</option>
        </select>
        <input
          className="input-style py-1.5 px-2"
          placeholder="Resource ID"
          value={locked?.resource_id ? locked.resource_id : resourceId}
          disabled={Boolean(locked?.resource_id)}
          onChange={(e) => {
            setResourceId(e.target.value);
            setOffset(0);
          }}
        />
        <select
          className="select-style"
          value={success}
          onChange={(e) => {
            setSuccess(e.target.value as '' | 'true' | 'false');
            setOffset(0);
          }}
        >
          <option value="">Success / failure</option>
          <option value="true">Success</option>
          <option value="false">Failure</option>
        </select>
        <div className="flex gap-2 col-span-2 md:col-span-2 xl:col-span-1">
          <input type="date" className="input-style py-1.5 px-2" value={start} onChange={(e) => { setStart(e.target.value); setOffset(0); }} />
          <input type="date" className="input-style py-1.5 px-2" value={end} onChange={(e) => { setEnd(e.target.value); setOffset(0); }} />
        </div>
      </div>

      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex-1 min-h-0 flex flex-col">
        <div className="overflow-auto flex-1 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 size={20} className="animate-spin mr-2" /> Loading…
            </div>
          ) : error ? (
            <div className="py-12 text-center text-gray-500">{error}</div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-gray-500">{emptyMessage}</div>
          ) : (
            <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300 min-w-[56rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400 sticky top-0">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Action</th>
                  <th className="px-4 py-3">Resource</th>
                  <th className="px-4 py-3">Result</th>
                  <th className="px-4 py-3">IP</th>
                  <th className="px-4 py-3">Details</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.id}
                    className="border-b border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/20 cursor-pointer"
                    onClick={() => setSelected(item)}
                  >
                    <td className="px-4 py-3 whitespace-nowrap">{formatLocalDateTime(item.timestamp)}</td>
                    <td className="px-4 py-3 font-medium text-gray-900 dark:text-white">{actorDisplayName(item)}</td>
                    <td className="px-4 py-3">{displayRole(item.actor_role)}</td>
                    <td className="px-4 py-3">{item.action}</td>
                    <td className="px-4 py-3">{item.resource_label || item.resource_id || item.resource_type || '—'}</td>
                    <td className="px-4 py-3">
                      <span className={item.success === false ? 'text-red-500' : 'text-green-600 dark:text-green-400'}>
                        {item.success === false ? 'Failure' : 'Success'}
                      </span>
                      {loginFailureLabel(item.metadata) ? (
                        <span className="block text-xs text-gray-500">{loginFailureLabel(item.metadata)}</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">{item.ip_address || '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-500">{summarizeUserAgent(item.user_agent)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="flex items-center justify-between px-4 py-2 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500">
          <span>{total === 0 ? '0 records' : `${from}–${to} of ${total}`}</span>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-secondary px-3 py-1 text-xs w-auto"
              disabled={!canPrev || loading}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Previous
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1 text-xs w-auto"
              disabled={!canNext || loading}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </button>
          </div>
        </div>
      </div>
      <AuditDetailModal item={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
