import React, { useState } from 'react';
import toast from 'react-hot-toast';
import { Lock, User as UserIcon } from 'lucide-react';
import { authService } from '../services/authService';
import type { User } from '../services/authService';

interface LoginPageProps {
  onLoginSuccess: (user: User) => void;
}

export default function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsLoading(true);

    try {
      const user = await authService.login(name, password);
      if (user) {
        onLoginSuccess(user);
        toast.success(`Welcome, ${user.name}!`);
      } else {
        throw new Error('Login failed');
      }
    } catch (err: unknown) {
      const error = err as Error;
      const errorMsg = error.message || 'Login failed. Please check your credentials.';
      toast.error(errorMsg);
      console.error('Login error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-100 dark:bg-gray-900">
      <div className="w-full max-w-md p-8 space-y-8 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-3xl font-extrabold text-center text-gray-900 dark:text-white">
            VMS Login
          </h2>
        </div>
        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          
          {/* Username Input */}
          <div className="relative">
            <UserIcon className="absolute top-1/2 left-3 -translate-y-1/2 h-5 w-5 text-gray-400 dark:text-gray-500" />
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-style py-2.5 pr-4 pl-10"
              placeholder="Username"
            />
          </div>

          {/* Password Input */}
          <div className="relative">
            <Lock className="absolute top-1/2 left-3 -translate-y-1/2 h-5 w-5 text-gray-400 dark:text-gray-500" />
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-style py-2.5 pr-4 pl-10"
              placeholder="Password"
            />
          </div>

          {/* Sign In Button */}
          <div>
            <button
              type="submit"
              disabled={isLoading}
              className="btn-primary"
            >
              {isLoading ? 'Signing in...' : 'Sign In'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
