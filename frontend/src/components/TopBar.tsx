import React, { useState } from 'react';
import ThemeToggle from './ThemeToggle';
import { User as UserIcon, LogOut, ChevronDown } from 'lucide-react';

interface TopBarProps {
  userName: string;
  userRole: string;
  onLogout: () => void;
  theme: 'light' | 'dark';
  toggleTheme: () => void;
}

export default function TopBar({ userName, userRole, onLogout, theme, toggleTheme }: TopBarProps) {
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);

  return (
    <header className="relative flex-shrink-0 flex items-center justify-end h-14 px-4 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <h1 className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-lg font-semibold text-gray-900 dark:text-white tracking-wide pointer-events-none">
        NVR System
      </h1>
      <div className="flex items-center space-x-4">
        <ThemeToggle theme={theme} toggleTheme={toggleTheme} />

        <div className="relative">
          <button
            onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
            className="flex items-center space-x-2 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
          >
            <UserIcon size={20} />
            <span className="text-sm font-medium hidden sm:block">{userRole}</span>
            <ChevronDown size={16} />
          </button>

          {isUserMenuOpen && (
            <div className="absolute right-0 mt-2 w-48 bg-white dark:bg-gray-800 rounded-md shadow-lg py-1 border border-gray-200 dark:border-gray-700 z-10">
              <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-700">
                <p className="text-sm font-medium text-gray-900 dark:text-white truncate">{userName}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">{userRole}</p>
              </div>
              <button
                onClick={onLogout}
                className="w-full text-left flex items-center px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <LogOut size={16} className="mr-2" />
                Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}