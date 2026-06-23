import React, { useState, useEffect } from 'react';
import toast from 'react-hot-toast';
import { apiFetch } from '../lib/api';
import { Plus, Edit, Trash2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import AddUserModal from '../components/AddUserModal';

interface CameraAccess {
  allowedCameraGroups: string[];
  allowedCameraUids: string[];
  /** Legacy full-access flag preserved until admin assigns specific floors/cameras. */
  accessType?: 'all';
}

interface User {
  id: string;
  name: string;
  role: string;
  lastLogin: string;
  status: string;
  email?: string;
  permissions?: string[];
  password?: string;
  cameraAccess?: CameraAccess;
}

export type { User, CameraAccess };

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [isAddUserModalOpen, setIsAddUserModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);

  useEffect(() => {
    fetchUsers();
  }, []);

  const fetchUsers = async () => {
    try {
      const response = await fetch('/api/users');
      if (!response.ok) throw new Error('Failed to fetch users');
      const data = await response.json();
      setUsers(data);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to fetch users');
    }
  };

  const handleAddUser = async (userData: User) => {
    try {
      const { id: _id, ...payload } = userData;
      const response = await fetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Failed to add user');
      toast.success('User added successfully');
      fetchUsers();
      setIsAddUserModalOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add user');
    }
  };

  const handleEditUser = async (userData: User) => {
    try {
      if (!editingUser) return;
      const response = await fetch(`/api/users/${editingUser.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(userData),
      });
      if (!response.ok) throw new Error('Failed to update user');
      toast.success('User updated successfully');
      fetchUsers();
      setEditingUser(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update user');
    }
  };

  const handleDeleteUser = async (userId: string) => {
    if (!confirm('Are you sure you want to delete this user?')) return;
    try {
      const response = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('Failed to delete user');
      toast.success('User deleted successfully');
      fetchUsers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete user');
    }
  };

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="User Management"
        subtitle="Add, edit, and manage user roles and permissions"
        rightContent={
          <button onClick={() => setIsAddUserModalOpen(true)} className="btn-primary flex items-center w-auto">
            <Plus size={18} className="mr-2" /> New User
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-4">
        <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-x-auto">
        <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300">
          <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
            <tr>
              <th scope="col" className="px-6 py-3">User</th>
              <th scope="col" className="px-6 py-3">Role</th>
              <th scope="col" className="px-6 py-3">Last Login</th>
              <th scope="col" className="px-6 py-3">Status</th>
              <th scope="col" className="px-6 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id} className="border-b border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/20">
                <td className="px-6 py-4 font-bold text-gray-900 dark:text-white">{user.name}</td>
                <td className="px-6 py-4">{user.role}</td>
                <td className="px-6 py-4">{user.lastLogin || 'N/A'}</td>
                <td className="px-6 py-4">
                  <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                    user.status === 'Active' ? 'bg-green-100 text-green-800 dark:bg-green-500/20 dark:text-green-300' : 'bg-gray-200 text-gray-800 dark:bg-gray-500/20 dark:text-gray-300'
                  }`}>
                    {user.status}
                  </span>
                </td>
                <td className="px-6 py-4 flex items-center justify-end space-x-2">
                  <button onClick={() => setEditingUser(user)} className="p-2 text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md" title="Edit User"><Edit size={16} /></button>
                  <button onClick={() => handleDeleteUser(user.id)} className="p-2 text-gray-500 dark:text-gray-400 hover:text-red-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-md" title="Delete User"><Trash2 size={16} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
      <AddUserModal
        isOpen={isAddUserModalOpen || !!editingUser}
        onClose={() => {
          setIsAddUserModalOpen(false);
          setEditingUser(null);
        }}
        onSave={editingUser ? handleEditUser : handleAddUser}
        user={editingUser}
      />
    </div>
  );
}
