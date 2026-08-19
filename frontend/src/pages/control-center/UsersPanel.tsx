import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { Edit, Plus, Trash2, Activity, Ban, CheckCircle } from 'lucide-react';
import {
  ControlCenterRequestError,
  createManagedUser,
  deleteManagedUser,
  fetchManagedUsers,
  updateManagedUser,
  type ManagedUser,
} from '../../lib/controlCenterApi';
import { ALL_PERMISSIONS } from '../../lib/permissions';
import { displayRole, isSuperAdminRole } from '../../lib/superAdmin';
import ConfirmModal from '../../components/control-center/ConfirmModal';
import ControlCenterUserModal from '../../components/control-center/ControlCenterUserModal';
import type { User } from '../../services/authService';

export default function UsersPanel({
  currentUser,
  onViewActivity,
}: {
  currentUser: User;
  onViewActivity: (user: ManagedUser) => void;
}): React.ReactElement {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    body: React.ReactNode;
    label: string;
    danger?: boolean;
    run: () => Promise<void>;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await fetchManagedUsers());
    } catch (err) {
      const message = err instanceof ControlCenterRequestError ? err.message : 'Could not load users.';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const isSelf = (user: ManagedUser) => user.id === currentUser.id;

  const runConfirmed = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      await confirm.run();
      setConfirm(null);
      await load();
    } catch (err) {
      toast.error(err instanceof ControlCenterRequestError ? err.message : 'The request could not be completed.');
    } finally {
      setBusy(false);
    }
  };

  const persistUser = async (payload: ManagedUser & { password?: string }) => {
    const body: Record<string, unknown> = {
      name: payload.name,
      email: payload.email,
      role: payload.role,
      status: payload.status,
      permissions:
        isSuperAdminRole(payload.role) && !(payload.permissions || []).length
          ? [...ALL_PERMISSIONS]
          : payload.permissions,
      cameraAccess: payload.cameraAccess,
    };
    if (payload.password) body.password = payload.password;
    if (editing) {
      await updateManagedUser(editing.id, body);
      toast.success('User updated');
    } else {
      await createManagedUser(body);
      toast.success('User created');
    }
    setModalOpen(false);
    setEditing(null);
    await load();
  };

  const handleSave = (payload: ManagedUser & { password?: string }) => {
    if (!editing) {
      void persistUser(payload).catch((err) => {
        toast.error(err instanceof ControlCenterRequestError ? err.message : 'Could not create user.');
      });
      return;
    }
    const roleChanged = payload.role !== editing.role;
    const disabling = (payload.status || '').toLowerCase() === 'disabled' && (editing.status || 'Active').toLowerCase() !== 'disabled';
    if (isSelf(editing) && (roleChanged || disabling)) {
      toast.error('This account cannot disable or demote itself from here.');
      return;
    }
    if (roleChanged || disabling) {
      setConfirm({
        title: roleChanged ? 'Change role?' : `Disable ${editing.name}?`,
        body: roleChanged ? (
          <div>
            <p>Current: <strong>{displayRole(editing.role)}</strong></p>
            <p>New: <strong>{displayRole(payload.role)}</strong></p>
          </div>
        ) : (
          <p>This account will no longer be able to sign in.</p>
        ),
        label: roleChanged ? 'Change Role' : 'Disable',
        danger: !roleChanged,
        run: async () => {
          await persistUser(payload);
        },
      });
      return;
    }
    void persistUser(payload).catch((err) => {
      toast.error(err instanceof ControlCenterRequestError ? err.message : 'Could not update user.');
    });
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex justify-end mb-3">
        <button type="button" onClick={() => { setEditing(null); setModalOpen(true); }} className="btn-primary flex items-center w-auto">
          <Plus size={16} className="mr-2" /> New User
        </button>
      </div>
      <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex-1 min-h-0">
        <div className="overflow-auto h-full">
          {loading ? (
            <p className="py-12 text-center text-gray-500">Loading users…</p>
          ) : error ? (
            <p className="py-12 text-center text-gray-500">{error}</p>
          ) : users.length === 0 ? (
            <p className="py-12 text-center text-gray-500">No users returned.</p>
          ) : (
            <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300 min-w-[48rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
                <tr>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Last login</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const self = isSelf(user);
                  const disabled = (user.status || 'Active').toLowerCase() === 'disabled';
                  return (
                    <tr key={user.id} className="border-b border-gray-200 dark:border-gray-700">
                      <td className="px-4 py-3 font-medium text-gray-900 dark:text-white">
                        {user.name}
                        {self ? <span className="ml-2 text-xs text-gray-500">(you)</span> : null}
                      </td>
                      <td className="px-4 py-3">{displayRole(user.role)}</td>
                      <td className="px-4 py-3">{user.lastLogin || '—'}</td>
                      <td className="px-4 py-3">{user.status || 'Active'}</td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <button type="button" className="p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700" title="View activity" onClick={() => onViewActivity(user)}>
                            <Activity size={16} />
                          </button>
                          <button type="button" className="p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700" title="Edit" onClick={() => { setEditing(user); setModalOpen(true); }}>
                            <Edit size={16} />
                          </button>
                          {!self && (
                            <button
                              type="button"
                              className="p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700"
                              title={disabled ? 'Enable' : 'Disable'}
                              onClick={() =>
                                setConfirm({
                                  title: disabled ? `Enable ${user.name}?` : `Disable ${user.name}?`,
                                  body: disabled
                                    ? <p>This account will be able to sign in again.</p>
                                    : <p>This account will no longer be able to sign in.</p>,
                                  label: disabled ? 'Enable' : 'Disable',
                                  danger: !disabled,
                                  run: async () => {
                                    await updateManagedUser(user.id, { status: disabled ? 'Active' : 'Disabled' });
                                    toast.success(disabled ? 'User enabled' : 'User disabled');
                                  },
                                })
                              }
                            >
                              {disabled ? <CheckCircle size={16} /> : <Ban size={16} />}
                            </button>
                          )}
                          {!self && (
                            <button
                              type="button"
                              className="p-2 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 text-red-500"
                              title="Delete"
                              onClick={() =>
                                setConfirm({
                                  title: `Delete ${user.name}?`,
                                  body: <p>This permanently removes the account.</p>,
                                  label: 'Delete',
                                  danger: true,
                                  run: async () => {
                                    await deleteManagedUser(user.id);
                                    toast.success('User deleted');
                                  },
                                })
                              }
                            >
                              <Trash2 size={16} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <ControlCenterUserModal
        isOpen={modalOpen}
        onClose={() => { setModalOpen(false); setEditing(null); }}
        onSave={handleSave}
        user={editing}
        lockRole={Boolean(editing && isSelf(editing))}
      />
      <ConfirmModal
        open={Boolean(confirm)}
        title={confirm?.title || ''}
        body={confirm?.body}
        confirmLabel={confirm?.label || 'Confirm'}
        danger={confirm?.danger}
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void runConfirmed()}
      />
    </div>
  );
}
