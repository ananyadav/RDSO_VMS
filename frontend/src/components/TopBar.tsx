import React, { useState, useEffect, useRef } from 'react';
import ThemeToggle from './ThemeToggle';
import { User as UserIcon, LogOut, ChevronDown } from 'lucide-react';
import { displayRole } from '../lib/superAdmin';

interface TopBarProps {
  userName: string;
  userRole: string;
  onLogout: () => void;
  theme: 'light' | 'dark';
  toggleTheme: () => void;
}

export default function TopBar({ userName, userRole, onLogout, theme, toggleTheme }: TopBarProps) {
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isUserMenuOpen) return;

    const onPointerDown = (e: MouseEvent | TouchEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
    };
  }, [isUserMenuOpen]);

  const handleLogout = () => {
    setIsUserMenuOpen(false);
    onLogout();
  };

  return (
    <header className="relative z-50 flex-shrink-0 flex items-center justify-end h-11 px-3 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <h1 className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-base font-semibold text-gray-900 dark:text-white tracking-wide pointer-events-none">
        VMS System
      </h1>
      <div className="flex items-center space-x-4">
        <ThemeToggle theme={theme} toggleTheme={toggleTheme} />

        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setIsUserMenuOpen((open) => !open)}
            className="flex items-center space-x-2 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
            aria-expanded={isUserMenuOpen}
            aria-haspopup="menu"
          >
            <UserIcon size={20} />
            <span className="text-sm font-medium hidden sm:block">{displayRole(userRole)}</span>
            <ChevronDown size={16} className={isUserMenuOpen ? 'rotate-180 transition-transform' : 'transition-transform'} />
          </button>

          {isUserMenuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full mt-2 w-48 bg-white dark:bg-gray-800 rounded-md shadow-xl py-1 border border-gray-200 dark:border-gray-700 z-[100]"
            >
              <div className="px-4 py-2 border-b border-gray-200 dark:border-gray-700">
                <p className="text-sm font-medium text-gray-900 dark:text-white truncate">{userName}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">{displayRole(userRole)}</p>
              </div>
              <button
                type="button"
                role="menuitem"
                onClick={handleLogout}
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
