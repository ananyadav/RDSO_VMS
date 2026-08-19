import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ClipboardList,
  HeartPulse,
  LayoutDashboard,
  MonitorSmartphone,
  Shield,
  Users,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import OverviewPanel from './control-center/OverviewPanel';
import UsersPanel from './control-center/UsersPanel';
import SessionsPanel from './control-center/SessionsPanel';
import HealthPanel from './control-center/HealthPanel';
import AuditLogTable from '../components/control-center/AuditLogTable';
import {
  useUrlHydration,
  useUrlSync,
  initialEnumParam,
  initialStringParam,
} from '../hooks/useUrlSearchState';
import { fetchManagedUsers, type ManagedUser } from '../lib/controlCenterApi';
import type { User } from '../services/authService';

type ControlTab = 'overview' | 'users' | 'login' | 'audit' | 'sessions' | 'health';

const TABS: { id: ControlTab; label: string; icon: React.ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <LayoutDashboard size={16} /> },
  { id: 'users', label: 'Users', icon: <Users size={16} /> },
  { id: 'login', label: 'Login History', icon: <Shield size={16} /> },
  { id: 'audit', label: 'Audit Logs', icon: <ClipboardList size={16} /> },
  { id: 'sessions', label: 'Active Sessions', icon: <MonitorSmartphone size={16} /> },
  { id: 'health', label: 'System Health', icon: <HeartPulse size={16} /> },
];

export default function ControlCenter({ currentUser }: { currentUser: User }): React.ReactElement {
  const { setParams, params, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [activeTab, setActiveTab] = useState<ControlTab>(() =>
    initialEnumParam(initialParams, 'tab', TABS.map((t) => t.id) as ControlTab[], 'overview'),
  );
  const [auditPreset, setAuditPreset] = useState<'all' | 'camera' | 'location'>(() => {
    const type = initialStringParam(initialParams, 'resource_type');
    if (type === 'camera') return 'camera';
    if (type === 'location') return 'location';
    return 'all';
  });
  const [activityUser, setActivityUser] = useState(initialStringParam(initialParams, 'user'));
  const [users, setUsers] = useState<ManagedUser[]>([]);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  useEffect(() => {
    const tab = params.get('tab');
    if (tab && TABS.some((t) => t.id === tab) && tab !== activeTab) {
      setActiveTab(tab as ControlTab);
    }
    const user = params.get('user') || '';
    if (user !== activityUser) setActivityUser(user);
    const type = params.get('resource_type');
    if (type === 'camera' || type === 'location') setAuditPreset(type);
  }, [params, activeTab, activityUser]);

  useEffect(() => {
    void fetchManagedUsers()
      .then(setUsers)
      .catch(() => setUsers([]));
  }, []);

  const urlValues = useMemo(
    () => ({
      tab: activeTab === 'overview' ? null : activeTab,
      user: activeTab === 'audit' && activityUser ? activityUser : null,
      resource_type:
        activeTab === 'audit' && auditPreset !== 'all' ? auditPreset : null,
    }),
    [activeTab, activityUser, auditPreset],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  const userOptions = users.map((u) => ({ id: u.id, name: u.name }));
  const lockedUser = activeTab === 'audit' && activityUser ? { user: activityUser } : undefined;

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 pt-3">
        <PageHeader
          title="Control Center"
          subtitle="Accounts, activity, sessions, and platform health"
        />
        <div className="flex items-center gap-1.5 bg-gray-800/80 border border-gray-700 rounded-lg p-1.5 overflow-x-auto mb-3">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => {
                setActiveTab(tab.id);
                if (tab.id !== 'audit') {
                  setActivityUser('');
                  setAuditPreset('all');
                }
              }}
              className={`inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm font-medium whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-700/60'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden px-4 pb-4 flex flex-col">
        {activeTab === 'overview' && (
          <div className="flex-1 overflow-y-auto">
            <OverviewPanel />
          </div>
        )}
        {activeTab === 'users' && (
          <UsersPanel
            currentUser={currentUser}
            onViewActivity={(user) => {
              setActivityUser(user.id);
              setAuditPreset('all');
              setActiveTab('audit');
            }}
          />
        )}
        {activeTab === 'login' && (
          <AuditLogTable
            users={userOptions}
            locked={{ resource_type: 'auth' }}
            actionsOnly={['LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT']}
            emptyMessage="No login events for these filters."
          />
        )}
        {activeTab === 'audit' && (
          <div className="flex flex-col min-h-0 flex-1 gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {(['all', 'camera', 'location'] as const).map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => {
                    setAuditPreset(preset);
                    if (preset !== 'all') setActivityUser('');
                  }}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium ${
                    auditPreset === preset
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-800 text-gray-300 border border-gray-700'
                  }`}
                >
                  {preset === 'all' ? 'All events' : preset === 'camera' ? 'Camera history' : 'Location history'}
                </button>
              ))}
              {activityUser ? (
                <button
                  type="button"
                  className="px-3 py-1.5 rounded-md text-xs bg-gray-800 text-gray-300 border border-gray-700"
                  onClick={() => setActivityUser('')}
                >
                  Clear user filter
                </button>
              ) : null}
            </div>
            {activityUser ? (
              <p className="text-xs text-gray-400 flex items-center gap-1">
                <Activity size={12} /> Showing activity for selected user
              </p>
            ) : null}
            <AuditLogTable
              users={userOptions}
              locked={{
                ...lockedUser,
                resource_type: auditPreset === 'all' ? undefined : auditPreset,
              }}
              extraActions={
                auditPreset === 'location'
                  ? ['LOCATION_CREATED', 'LOCATION_UPDATED', 'LOCATION_DELETED', 'CAMERA_LOCATION_CHANGED']
                  : auditPreset === 'camera'
                    ? ['CAMERA_CREATED', 'CAMERA_UPDATED', 'CAMERA_DELETED', 'CAMERA_LOCATION_CHANGED']
                    : undefined
              }
            />
          </div>
        )}
        {activeTab === 'sessions' && <SessionsPanel currentUser={currentUser} />}
        {activeTab === 'health' && (
          <div className="flex-1 overflow-y-auto">
            <HealthPanel />
          </div>
        )}
      </div>
    </div>
  );
}
