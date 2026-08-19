import React, { useState, useEffect } from 'react';
import { X, UserPlus } from 'lucide-react';
import toast from 'react-hot-toast';
import type { User, CameraAccess } from '../pages/UserManagement';
import CameraAccessPicker, { normalizeStoredCameraAccess } from './CameraAccessPicker';
import { ALL_PERMISSIONS, PERMISSIONS, permissionLabel } from '../lib/permissions';

interface UserModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (user: User) => void;
  user: User | null;
}

const allPermissions = ALL_PERMISSIONS;

const defaultAccess: CameraAccess = {
  allowedCameraGroups: [],
  allowedCameraUids: [],
};

export default function AddUserModal({ isOpen, onClose, onSave, user }: UserModalProps): React.ReactElement | null {
  const [formData, setFormData] = useState<Partial<User>>({});
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [activeTab, setActiveTab] = useState('info');

  const isEditing = !!user;
  const isAdminRole = (formData.role || 'Viewer') === 'Admin';
  const cameraAccess = formData.cameraAccess ?? defaultAccess;

  useEffect(() => {
    if (isOpen) {
      setActiveTab('info');
      setPassword('');
      setConfirmPassword('');
      if (user) {
        const normalized = normalizeStoredCameraAccess(user.cameraAccess);
        setFormData({
          ...user,
          permissions: user.permissions || [],
          cameraAccess: normalized,
        });
      } else {
        setFormData({
          name: '',
          email: '',
          role: 'Viewer',
          status: 'Active',
          lastLogin: 'Never',
          permissions: [PERMISSIONS.LIVE_VIEW],
          cameraAccess: defaultAccess,
        });
      }
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handlePermissionChange = (permission: string) => {
    setFormData((prev) => {
      const currentPermissions = prev.permissions || [];
      const newPermissions = currentPermissions.includes(permission)
        ? currentPermissions.filter((p) => p !== permission)
        : [...currentPermissions, permission];
      return { ...prev, permissions: newPermissions };
    });
  };

  const handleCameraAccessChange = (groups: string[], uids: string[]) => {
    setFormData((prev) => ({
      ...prev,
      cameraAccess: {
        allowedCameraGroups: groups,
        allowedCameraUids: uids,
      },
    }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) return toast.error('Username is required.');
    if (!isEditing && !password) return toast.error('Password is required for new users.');
    if (password !== confirmPassword) return toast.error('Passwords do not match.');

    const userToSave: User = {
      ...(formData as User),
      id: formData.id || '',
      password: password || undefined,
      email: formData.email || '',
      lastLogin: formData.lastLogin || 'Never',
      status: formData.status || 'Active',
      permissions: formData.permissions || [],
      cameraAccess: isAdminRole
        ? defaultAccess
        : cameraAccess.accessType === 'all' &&
            !cameraAccess.allowedCameraGroups.length &&
            !cameraAccess.allowedCameraUids.length
          ? { accessType: 'all' as const, allowedCameraGroups: [], allowedCameraUids: [] }
          : {
              allowedCameraGroups: cameraAccess.allowedCameraGroups,
              allowedCameraUids: cameraAccess.allowedCameraUids,
            },
    };

    onSave(userToSave);
  };

  const modalWide = activeTab === 'cameras' && !isAdminRole;

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
              {isEditing ? `Edit User: ${user?.name}` : 'Add New User'}
            </h3>
            <button type="button" onClick={onClose} className="text-gray-400 hover:text-white"><X size={24} /></button>
          </div>

          <div className="flex border-b border-gray-700 shrink-0 overflow-x-auto">
            <button type="button" onClick={() => setActiveTab('info')} className={`px-4 py-2 text-sm font-medium whitespace-nowrap ${activeTab === 'info' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Basic Info</button>
            <button type="button" onClick={() => setActiveTab('permissions')} className={`px-4 py-2 text-sm font-medium whitespace-nowrap ${activeTab === 'permissions' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Permissions</button>
            <button type="button" onClick={() => setActiveTab('cameras')} className={`px-4 py-2 text-sm font-medium whitespace-nowrap ${activeTab === 'cameras' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Camera Access</button>
            {isEditing && (
              <button type="button" onClick={() => setActiveTab('password')} className={`px-4 py-2 text-sm font-medium whitespace-nowrap ${activeTab === 'password' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Password</button>
            )}
          </div>

          <div className="p-6 overflow-y-auto flex-1 min-h-0">
            {activeTab === 'info' && (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Username</label>
                  <input type="text" name="name" value={formData.name || ''} onChange={handleChange} className="input-style py-2.5 px-3" required />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-1">Email (Optional)</label>
                  <input type="email" name="email" value={formData.email || ''} onChange={handleChange} className="input-style py-2.5 px-3" />
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
                  <select name="role" value={formData.role || 'Viewer'} onChange={handleChange} className="select-style">
                    <option>Operator</option>
                    <option>Viewer</option>
                  </select>
                </div>
              </div>
            )}

            {activeTab === 'permissions' && (
              <div className="grid grid-cols-2 gap-4">
                {allPermissions.map((permission) => (
                  <label key={permission} className="flex items-center space-x-3 cursor-pointer">
                    <input type="checkbox" checked={formData.permissions?.includes(permission)} onChange={() => handlePermissionChange(permission)} className="checkbox-style" />
                    <span className="text-gray-300">{permissionLabel(permission)}</span>
                  </label>
                ))}
              </div>
            )}

            {activeTab === 'cameras' && (
              <div>
                {isAdminRole ? (
                  <p className="text-sm text-gray-400">Admin users can access all buildings, floors, and cameras in Live View and PTZ.</p>
                ) : cameraAccess.accessType === 'all' ? (
                  <div className="space-y-3">
                    <p className="text-sm text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
                      This user currently has access to all cameras (legacy setting). Use the picker
                      below to assign specific floors or cameras — that will replace full access.
                    </p>
                    <CameraAccessPicker
                      allowedCameraGroups={cameraAccess.allowedCameraGroups}
                      allowedCameraUids={cameraAccess.allowedCameraUids}
                      onChange={handleCameraAccessChange}
                    />
                  </div>
                ) : (
                  <div className="space-y-3">
                    <p className="text-sm text-gray-400">
                      Operator and Viewer only see assigned cameras in Live View and PTZ.
                    </p>
                    <CameraAccessPicker
                      allowedCameraGroups={cameraAccess.allowedCameraGroups}
                      allowedCameraUids={cameraAccess.allowedCameraUids}
                      onChange={handleCameraAccessChange}
                    />
                  </div>
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
