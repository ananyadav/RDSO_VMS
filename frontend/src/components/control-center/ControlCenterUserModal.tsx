import React, { useEffect, useState } from 'react';
import { X, UserPlus } from 'lucide-react';
import toast from 'react-hot-toast';
import CameraAccessPicker, { normalizeStoredCameraAccess } from '../CameraAccessPicker';
import { ALL_PERMISSIONS, permissionLabel } from '../../lib/permissions';
import { isSuperAdminRole } from '../../lib/superAdmin';
import type { ManagedUser } from '../../lib/controlCenterApi';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSave: (user: ManagedUser & { password?: string }) => void;
  user: ManagedUser | null;
  lockRole?: boolean;
}

const defaultAccess = {
  allowedCameraGroups: [] as string[],
  allowedCameraUids: [] as string[],
};

export default function ControlCenterUserModal({
  isOpen,
  onClose,
  onSave,
  user,
  lockRole = false,
}: Props): React.ReactElement | null {
  const [formData, setFormData] = useState<Partial<ManagedUser>>({});
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [activeTab, setActiveTab] = useState('info');

  const isEditing = !!user;
  const role = formData.role || 'Operator';
  const fullAccess = role === 'Admin' || isSuperAdminRole(role);
  const cameraAccess = formData.cameraAccess ?? defaultAccess;

  useEffect(() => {
    if (!isOpen) return;
    setActiveTab('info');
    setPassword('');
    setConfirmPassword('');
    if (user) {
      setFormData({
        ...user,
        permissions: user.permissions || [],
        cameraAccess: normalizeStoredCameraAccess(user.cameraAccess),
      });
    } else {
      setFormData({
        name: '',
        email: '',
        role: 'Operator',
        status: 'Active',
        permissions: [],
        cameraAccess: defaultAccess,
      });
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) return toast.error('Username is required.');
    if (!isEditing && !password) return toast.error('Password is required for new users.');
    if (password !== confirmPassword) return toast.error('Passwords do not match.');

    onSave({
      ...(formData as ManagedUser),
      id: formData.id || '',
      password: password || undefined,
      email: formData.email || '',
      status: formData.status || 'Active',
      permissions: formData.permissions || [],
      cameraAccess: fullAccess
        ? defaultAccess
        : {
            allowedCameraGroups: cameraAccess.allowedCameraGroups || [],
            allowedCameraUids: cameraAccess.allowedCameraUids || [],
          },
    });
  };

  const modalWide = activeTab === 'cameras' && !fullAccess;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div
        className={`bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-h-[92vh] flex flex-col ${
          modalWide ? 'max-w-6xl' : 'max-w-lg'
        }`}
      >
        <form onSubmit={handleSubmit} className="flex flex-col min-h-0 flex-1">
          <div className="flex items-center justify-between p-4 border-b border-gray-700 shrink-0">
            <h3 className="text-xl font-bold text-white flex items-center">
              <UserPlus size={20} className="mr-3" />
              {isEditing ? `Edit User: ${user?.name}` : 'Add User'}
            </h3>
            <button type="button" onClick={onClose} className="text-gray-400 hover:text-white"><X size={24} /></button>
          </div>

          <div className="flex border-b border-gray-700 shrink-0 overflow-x-auto">
            {['info', 'permissions', 'cameras'].map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium whitespace-nowrap capitalize ${
                  activeTab === tab ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'
                }`}
              >
                {tab === 'info' ? 'Basic Info' : tab === 'cameras' ? 'Camera Access' : 'Permissions'}
              </button>
            ))}
            {isEditing && (
              <button
                type="button"
                onClick={() => setActiveTab('password')}
                className={`px-4 py-2 text-sm font-medium whitespace-nowrap ${
                  activeTab === 'password' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'
                }`}
              >
                Password
              </button>
            )}
          </div>

          <div className="p-6 overflow-y-auto flex-1 min-h-0">
            {activeTab === 'info' && (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Username</label>
                  <input type="text" name="name" value={formData.name || ''} onChange={(e) => setFormData((p) => ({ ...p, name: e.target.value }))} className="input-style py-2.5 px-3" required />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Email (Optional)</label>
                  <input type="email" name="email" value={formData.email || ''} onChange={(e) => setFormData((p) => ({ ...p, email: e.target.value }))} className="input-style py-2.5 px-3" />
                </div>
                {!isEditing && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Password</label>
                      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="input-style py-2.5 px-3" required />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Confirm Password</label>
                      <input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="input-style py-2.5 px-3" required={!!password} />
                    </div>
                  </>
                )}
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Role</label>
                  <select
                    name="role"
                    value={formData.role || 'Operator'}
                    disabled={lockRole}
                    onChange={(e) =>
                      setFormData((p) => ({
                        ...p,
                        role: e.target.value,
                        permissions: isSuperAdminRole(e.target.value) ? [...ALL_PERMISSIONS] : p.permissions,
                      }))
                    }
                    className="select-style"
                  >
                    <option value="SUPER_ADMIN">Super Admin</option>
                    <option value="Admin">Admin</option>
                    <option value="Operator">Operator</option>
                    <option value="Viewer">Viewer</option>
                  </select>
                  {lockRole ? (
                    <p className="text-xs text-gray-500 mt-1">Role changes for this account are not offered here.</p>
                  ) : null}
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Status</label>
                  <select
                    name="status"
                    value={formData.status || 'Active'}
                    disabled={lockRole}
                    onChange={(e) => setFormData((p) => ({ ...p, status: e.target.value }))}
                    className="select-style"
                  >
                    <option value="Active">Active</option>
                    <option value="Disabled">Disabled</option>
                  </select>
                </div>
              </div>
            )}

            {activeTab === 'permissions' && (
              <div className="grid grid-cols-2 gap-4">
                {ALL_PERMISSIONS.map((permission) => (
                  <label key={permission} className="flex items-center space-x-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formData.permissions?.includes(permission)}
                      onChange={() =>
                        setFormData((prev) => {
                          const current = prev.permissions || [];
                          return {
                            ...prev,
                            permissions: current.includes(permission)
                              ? current.filter((p) => p !== permission)
                              : [...current, permission],
                          };
                        })
                      }
                      className="checkbox-style"
                    />
                    <span className="text-gray-300">{permissionLabel(permission)}</span>
                  </label>
                ))}
              </div>
            )}

            {activeTab === 'cameras' && (
              <div>
                {fullAccess ? (
                  <p className="text-sm text-gray-400">Admin and Super Admin accounts can access all cameras.</p>
                ) : (
                  <CameraAccessPicker
                    allowedCameraGroups={cameraAccess.allowedCameraGroups || []}
                    allowedCameraUids={cameraAccess.allowedCameraUids || []}
                    onChange={(groups, uids) =>
                      setFormData((prev) => ({
                        ...prev,
                        cameraAccess: { allowedCameraGroups: groups, allowedCameraUids: uids },
                      }))
                    }
                  />
                )}
              </div>
            )}

            {activeTab === 'password' && isEditing && (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">New Password</label>
                  <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="input-style py-2.5 px-3" placeholder="Leave blank to keep current" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Confirm New Password</label>
                  <input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="input-style py-2.5 px-3" required={!!password} />
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center justify-end p-4 border-t border-gray-700 space-x-2 shrink-0">
            <button type="button" onClick={onClose} className="btn-secondary px-4 py-2 text-sm w-auto">Cancel</button>
            <button type="submit" className="btn-primary px-4 py-2 text-sm w-auto">{isEditing ? 'Save Changes' : 'Add User'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
