import React, { useState, useEffect } from 'react';
import { X, UserPlus } from 'lucide-react';
import type { User } from '../pages/UserManagement';
import ToggleSwitch from './ToggleSwitch'; // Make sure you have this component

interface UserModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (user: User, newPassword?: string) => void;
  user: User | null;
}

const allPermissions = ['Live View', 'Playback', 'Events', 'Cameras', 'System', 'Users'];

export default function UserModal({ isOpen, onClose, onSave, user }: UserModalProps): React.ReactElement | null {
  const [formData, setFormData] = useState<Partial<User>>({});
  const [newPassword, setNewPassword] = useState('');
  const [activeTab, setActiveTab] = useState('info');

  const isEditing = !!user;

  useEffect(() => {
    // This effect runs when the modal is opened.
    // It pre-fills the form for editing or resets it for a new user.
    if (isOpen) {
      setActiveTab('info');
      setNewPassword('');
      if (user) {
        setFormData({ ...user, permissions: user.permissions || [] });
      } else {
        setFormData({ name: '', email: '', role: 'Viewer', status: 'Active', permissions: [] });
      }
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData((prev: Partial<User>) => ({ ...prev, [name]: value }));
  };

  const handlePermissionChange = (permission: string) => {
    setFormData((prev: Partial<User>) => {
      const currentPermissions = prev.permissions || [];
      const newPermissions = currentPermissions.includes(permission)
        ? currentPermissions.filter((p: string) => p !== permission)
        : [...currentPermissions, permission];
      return { ...prev, permissions: newPermissions };
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave(formData as User, newPassword);
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-lg">
        <form onSubmit={handleSubmit}>
          {/* Modal Header */}
          <div className="flex items-center justify-between p-4 border-b border-gray-700">
            <h3 className="text-xl font-bold text-white flex items-center">
              <UserPlus size={20} className="mr-3" />
              {isEditing ? `Edit User: ${user.name}` : 'Add New User'}
            </h3>
            <button type="button" onClick={onClose} className="text-gray-400 hover:text-white"><X size={24} /></button>
          </div>
          
          {/* Tabs */}
          <div className="flex border-b border-gray-700">
            <button type="button" onClick={() => setActiveTab('info')} className={`px-4 py-2 text-sm font-medium ${activeTab === 'info' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Basic Info</button>
            <button type="button" onClick={() => setActiveTab('permissions')} className={`px-4 py-2 text-sm font-medium ${activeTab === 'permissions' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Permissions</button>
            {isEditing && (
              <button type="button" onClick={() => setActiveTab('password')} className={`px-4 py-2 text-sm font-medium ${activeTab === 'password' ? 'text-white border-b-2 border-blue-500' : 'text-gray-400'}`}>Change Password</button>
            )}
          </div>

          {/* Modal Body */}
          <div className="p-6">
            {activeTab === 'info' && (
              <div className="space-y-4">
                <div>
                  <label className="label-style">Username</label>
                  <input type="text" name="name" value={formData.name || ''} onChange={handleChange} className="input-style" required />
                </div>
                 <div>
                  <label className="label-style">Email</label>
                  <input type="email" name="email" value={formData.email || ''} onChange={handleChange} className="input-style" required />
                </div>
                {!isEditing && (
                  <div>
                    <label className="label-style">Password</label>
                    <input type="password" name="password" onChange={e => setNewPassword(e.target.value)} className="input-style" required />
                  </div>
                )}
                <div>
                  <label className="label-style">Role</label>
                  <select name="role" value={formData.role || 'Viewer'} onChange={handleChange} className="input-style">
                    <option>Admin</option><option>Operator</option><option>Viewer</option>
                  </select>
                </div>
              </div>
            )}
            
            {activeTab === 'permissions' && (
              <div className="divide-y divide-gray-700">
                {allPermissions.map(permission => (
                  <ToggleSwitch
                    key={permission}
                    label={permission}
                    enabled={formData.permissions?.includes(permission) || false}
                    onChange={() => handlePermissionChange(permission)}
                  />
                ))}
              </div>
            )}

            {activeTab === 'password' && isEditing && (
              <div className="space-y-4">
                <div>
                  <label className="label-style">New Password</label>
                  <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} className="input-style" placeholder="Leave blank to keep current password" />
                </div>
                <p className="text-xs text-gray-500">Enter a new password for this user. The user will be required to use the new password at their next login.</p>
              </div>
            )}
          </div>

          {/* Modal Footer */}
          <div className="flex items-center justify-end p-4 border-t border-gray-700 space-x-2">
            <button type="button" onClick={onClose} className="btn-secondary px-4 py-2 text-sm">Cancel</button>
            <button type="submit" className="btn-primary px-4 py-2 text-sm">{isEditing ? 'Save Changes' : 'Add User'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
