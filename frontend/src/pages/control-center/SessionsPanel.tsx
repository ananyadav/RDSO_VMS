import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Loader2 } from 'lucide-react';
import {
  ControlCenterRequestError,
  fetchSessions,
  revokeUserSessions,
  type SessionItem,
} from '../../lib/controlCenterApi';
import { displayRole, formatLocalDateTime, summarizeUserAgent } from '../../lib/superAdmin';
import ConfirmModal from '../../components/control-center/ConfirmModal';
import type { User } from '../../services/authService';

const PAGE_SIZE = 50;

export default function SessionsPanel({ currentUser }: { currentUser: User }): React.ReactElement {
  const [items, setItems] = useState<SessionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<SessionItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [activeOnly, setActiveOnly] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchSessions({ active: activeOnly, limit: PAGE_SIZE, offset });
      const safe = (data.items || []).map((row) => {
        const copy = { ...row };
        delete (copy as { token?: unknown }).token;
        delete (copy as { cookie?: unknown }).cookie;
        return copy;
      });
      setItems(safe);
      setTotal(data.total || 0);
      setLimit(data.limit || PAGE_SIZE);
    } catch (err) {
      const message = err instanceof ControlCenterRequestError ? err.message : 'Could not load sessions.';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [activeOnly, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);

  const forceLogout = async () => {
    if (!target) return;
    setBusy(true);
    try {
      const result = await revokeUserSessions(target.user_id);
      toast.success(`Force logout completed (${result.revoked ?? 0} session${result.revoked === 1 ? '' : 's'}).`);
      setTarget(null);
      await load();
    } catch (err) {
      toast.error(err instanceof ControlCenterRequestError ? err.message : 'Force logout failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
          <input
            type="checkbox"
            className="checkbox-style"
            checked={activeOnly}
            onChange={(e) => {
              setActiveOnly(e.target.checked);
              setOffset(0);
            }}
          />
          Active only
        </label>
      </div>
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex-1 min-h-0 flex flex-col">
        <div className="overflow-auto flex-1">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 size={20} className="animate-spin mr-2" /> Loading…
            </div>
          ) : error ? (
            <p className="py-12 text-center text-gray-500">{error}</p>
          ) : items.length === 0 ? (
            <p className="py-12 text-center text-gray-500">No sessions found.</p>
          ) : (
            <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300 min-w-[56rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
                <tr>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Login</th>
                  <th className="px-4 py-3">Last activity</th>
                  <th className="px-4 py-3">IP</th>
                  <th className="px-4 py-3">Browser</th>
                  <th className="px-4 py-3">Expiry</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const self = row.user_id === currentUser.id;
                  return (
                    <tr key={row.id} className="border-b border-gray-200 dark:border-gray-700">
                      <td className="px-4 py-3 font-medium text-gray-900 dark:text-white">{row.user_name || '—'}</td>
                      <td className="px-4 py-3">{displayRole(row.role)}</td>
                      <td className="px-4 py-3 whitespace-nowrap">{formatLocalDateTime(row.created_at)}</td>
                      <td className="px-4 py-3 whitespace-nowrap">{formatLocalDateTime(row.last_seen_at)}</td>
                      <td className="px-4 py-3">{row.ip_address || '—'}</td>
                      <td className="px-4 py-3">{summarizeUserAgent(row.user_agent)}</td>
                      <td className="px-4 py-3 whitespace-nowrap">{formatLocalDateTime(row.expires_at)}</td>
                      <td className="px-4 py-3">{row.active ? 'Active' : row.revoked ? 'Revoked' : 'Expired'}</td>
                      <td className="px-4 py-3 text-right">
                        {!self && row.active ? (
                          <button
                            type="button"
                            className="text-red-500 hover:text-red-400 text-xs font-semibold"
                            onClick={() => setTarget(row)}
                          >
                            Force Logout
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="flex items-center justify-between px-4 py-2 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500">
          <span>{total === 0 ? '0 records' : `${from}–${to} of ${total}`}</span>
          <div className="flex gap-2">
            <button type="button" className="btn-secondary px-3 py-1 text-xs w-auto" disabled={offset <= 0 || loading} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button>
            <button type="button" className="btn-secondary px-3 py-1 text-xs w-auto" disabled={offset + limit >= total || loading} onClick={() => setOffset(offset + limit)}>Next</button>
          </div>
        </div>
      </div>
      <ConfirmModal
        open={Boolean(target)}
        title={`Force logout ${target?.user_name || 'this user'}?`}
        body={<p>This will invalidate their active session(s).</p>}
        confirmLabel="Force Logout"
        danger
        busy={busy}
        onCancel={() => setTarget(null)}
        onConfirm={() => void forceLogout()}
      />
    </div>
  );
}
