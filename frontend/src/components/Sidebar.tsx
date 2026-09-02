import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  Camera, Play, Calendar, Zap, SlidersHorizontal, HardDrive,
  Network, Users, Bell, Activity, Wrench, Radio, Shield, BellRing, ListOrdered, type LucideIcon,
} from 'lucide-react';
import type { User } from '../services/authService';
import { PERMISSIONS, type Permission, hasPermission, isAdminUser, isOpsAdminUser, isSuperAdminUser } from '../lib/permissions';

type NavItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  permission: Permission;
  adminOnly?: boolean;
  opsAdminOnly?: boolean;
  superAdminOnly?: boolean;
};

const mainNav: NavItem[] = [
  { label: 'Live View', href: '/live', icon: Camera, permission: PERMISSIONS.LIVE_VIEW },
  { label: 'Playback', href: '/playback', icon: Play, permission: PERMISSIONS.RECORDING_VIEW },
  { label: 'Events', href: '/events', icon: Calendar, permission: PERMISSIONS.EVENTS },
  { label: 'Alarm Rules', href: '/alarm-rules', icon: BellRing, permission: PERMISSIONS.EVENTS, opsAdminOnly: true },
  { label: 'PTZ', href: '/ptz', icon: Zap, permission: PERMISSIONS.LIVE_VIEW },
];

const configNav: NavItem[] = [
  { label: 'Cameras', href: '/camera-management', icon: SlidersHorizontal, permission: PERMISSIONS.CAMERAS, opsAdminOnly: true },
  { label: 'Camera Sequences', href: '/camera-sequences', icon: ListOrdered, permission: PERMISSIONS.CAMERAS, opsAdminOnly: true },
  { label: 'Storage', href: '/storage', icon: HardDrive, permission: PERMISSIONS.SYSTEM, superAdminOnly: true },
  { label: 'Network', href: '/network-settings', icon: Network, permission: PERMISSIONS.SYSTEM, superAdminOnly: true },
  { label: 'Users', href: '/user-management', icon: Users, permission: PERMISSIONS.USERS, adminOnly: true },
  { label: 'Alerts', href: '/notifications', icon: Bell, permission: PERMISSIONS.EVENTS },
];

const systemNav: NavItem[] = [
  { label: 'Status', href: '/system-status', icon: Activity, permission: PERMISSIONS.SYSTEM, superAdminOnly: true },
  { label: 'go2rtc', href: '/go2rtc-diagnostics', icon: Radio, permission: PERMISSIONS.SYSTEM, superAdminOnly: true },
  { label: 'Maintain', href: '/maintenance', icon: Wrench, permission: PERMISSIONS.SYSTEM, superAdminOnly: true },
];

function filterNav(items: NavItem[], user: User): NavItem[] {
  return items.filter((item) => {
    if (item.superAdminOnly && !isSuperAdminUser(user)) return false;
    if (item.opsAdminOnly && !isOpsAdminUser(user)) return false;
    if (item.adminOnly && !isAdminUser(user)) return false;
    if (item.href === '/user-management' && isSuperAdminUser(user)) return false;
    return hasPermission(user, item.permission);
  });
}

function NavItemLink({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.href}
      aria-label={item.label}
      className={(isActive: boolean) =>
        [
          'relative flex flex-col items-center justify-center gap-0.5 py-2 px-1 w-full transition-colors',
          "before:content-[''] before:absolute before:left-0 before:top-2 before:bottom-2 before:w-0.5 before:rounded-r",
          isActive
            ? '!text-white bg-white/10 before:bg-red-500'
            : 'text-gray-400 hover:text-gray-100 hover:bg-white/5 before:bg-transparent',
        ].join(' ')
      }
    >
      <Icon size={18} strokeWidth={1.75} className="flex-shrink-0" />
      <span className="text-[9px] leading-tight text-center font-medium px-0.5 max-w-[4.25rem]">
        {item.label}
      </span>
    </NavLink>
  );
}

interface SidebarProps {
  user: User;
}

export default function Sidebar({ user }: SidebarProps): React.ReactElement {
  const main = filterNav(mainNav, user);
  const config = filterNav(configNav, user);
  const system = filterNav(systemNav, user);

  return (
    <aside className="w-[4.25rem] flex-shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
      <nav className="flex-grow py-1 overflow-y-auto scrollbar-hide">
        {main.map((item) => (
          <NavItemLink key={item.href} item={item} />
        ))}

        {config.length > 0 && (
          <>
            <div className="my-1 mx-3 border-t border-gray-800" role="separator" />
            {config.map((item) => (
              <NavItemLink key={item.href} item={item} />
            ))}
          </>
        )}

        {system.length > 0 && (
          <>
            <div className="my-1 mx-3 border-t border-gray-800" role="separator" />
            {system.map((item) => (
              <NavItemLink key={item.href} item={item} />
            ))}
          </>
        )}

        {isSuperAdminUser(user) && (
          <>
            <div className="my-1 mx-3 border-t border-gray-800" role="separator" />
            <NavItemLink
              item={{
                label: 'Control Center',
                href: '/control-center',
                icon: Shield,
                permission: PERMISSIONS.SYSTEM,
              }}
            />
          </>
        )}
      </nav>
    </aside>
  );
}
