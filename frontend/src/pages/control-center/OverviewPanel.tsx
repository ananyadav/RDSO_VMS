import React, { useEffect, useState } from 'react';
import { Loader2, Users, MonitorSmartphone, Shield, Server, Radio, Activity } from 'lucide-react';
import toast from 'react-hot-toast';
import Card from '../../components/Card';
import {
  ControlCenterRequestError,
  fetchAuditLogs,
  fetchGo2RtcStatus,
  fetchHealthStatus,
  fetchManagedUsers,
  fetchSessions,
  type AuditLogItem,
  type Go2RtcStatus,
  type HealthStatus,
  type ManagedUser,
} from '../../lib/controlCenterApi';
import {
  actorDisplayName,
  displayRole,
  formatLocalDateTime,
  isSuperAdminRole,
  normalizeRoleKey,
} from '../../lib/superAdmin';

function countByRole(users: ManagedUser[], role: string): number {
  if (role === 'SUPER_ADMIN') return users.filter((u) => isSuperAdminRole(u.role)).length;
  return users.filter((u) => normalizeRoleKey(u.role) === normalizeRoleKey(role)).length;
}

export default function OverviewPanel(): React.ReactElement {
  const [users, setUsers] = useState<ManagedUser[] | null>(null);
  const [sessionTotal, setSessionTotal] = useState<number | null>(null);
  const [recent, setRecent] = useState<AuditLogItem[] | null>(null);
  const [failedLogins, setFailedLogins] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [go2rtc, setGo2rtc] = useState<Go2RtcStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      const start = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
      const wrap = <T,>(p: Promise<T>) =>
        p.then(
          (value): { status: 'fulfilled'; value: T } => ({ status: 'fulfilled', value }),
          (reason): { status: 'rejected'; reason: unknown } => ({ status: 'rejected', reason }),
        );
      const results = await Promise.all([
        wrap(fetchManagedUsers()),
        wrap(fetchSessions({ active: true, limit: 1, offset: 0 })),
        wrap(fetchAuditLogs({ limit: 8, offset: 0 })),
        wrap(fetchAuditLogs({ action: 'LOGIN_FAILED', success: 'false', start, limit: 1, offset: 0 })),
        wrap(fetchHealthStatus()),
        wrap(fetchGo2RtcStatus()),
      ]);
      if (cancelled) return;

      const take = <T,>(index: number, setter: (v: T) => void) => {
        const result = results[index];
        if (result.status === 'fulfilled') setter(result.value as T);
        else if (result.reason instanceof ControlCenterRequestError) toast.error(result.reason.message);
        else toast.error('Could not load Control Center overview.');
      };

      take(0, setUsers);
      const sessions = results[1];
      if (sessions.status === 'fulfilled') setSessionTotal(sessions.value.total);
      else toast.error(sessions.reason instanceof ControlCenterRequestError ? sessions.reason.message : 'Could not load sessions.');
      const recentRes = results[2];
      if (recentRes.status === 'fulfilled') setRecent(recentRes.value.items || []);
      else toast.error(recentRes.reason instanceof ControlCenterRequestError ? recentRes.reason.message : 'Could not load recent activity.');
      const failed = results[3];
      if (failed.status === 'fulfilled') setFailedLogins(failed.value.total);
      take(4, setHealth);
      take(5, setGo2rtc);
      setLoading(false);
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-400">
        <Loader2 className="animate-spin mr-2" size={20} /> Loading overview…
      </div>
    );
  }

  const activeUsers = users ? users.filter((u) => (u.status || 'Active').toLowerCase() !== 'disabled').length : null;
  const workers = go2rtc?.workers || [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        <Card>
          <div className="flex items-center gap-3 mb-2">
            <Users size={18} className="text-blue-400" />
            <h3 className="font-semibold text-white">Users</h3>
          </div>
          {users ? (
            <ul className="text-sm text-gray-300 space-y-1">
              <li>Active: {activeUsers}</li>
              <li>Admin: {countByRole(users, 'Admin')}</li>
              <li>Operator: {countByRole(users, 'Operator')}</li>
              <li>Super Admin: {countByRole(users, 'SUPER_ADMIN')}</li>
            </ul>
          ) : (
            <p className="text-sm text-gray-500">Unavailable</p>
          )}
        </Card>

        <Card>
          <div className="flex items-center gap-3 mb-2">
            <MonitorSmartphone size={18} className="text-emerald-400" />
            <h3 className="font-semibold text-white">Sessions</h3>
          </div>
          <p className="text-2xl font-bold text-white">{sessionTotal ?? '—'}</p>
          <p className="text-xs text-gray-400 mt-1">Active sessions</p>
        </Card>

        <Card>
          <div className="flex items-center gap-3 mb-2">
            <Shield size={18} className="text-amber-400" />
            <h3 className="font-semibold text-white">Security</h3>
          </div>
          <p className="text-2xl font-bold text-white">{failedLogins ?? '—'}</p>
          <p className="text-xs text-gray-400 mt-1">Failed logins (last 24 hours)</p>
        </Card>

        <Card>
          <div className="flex items-center gap-3 mb-2">
            <Server size={18} className="text-sky-400" />
            <h3 className="font-semibold text-white">System</h3>
          </div>
          {health ? (
            <ul className="text-sm text-gray-300 space-y-1">
              <li>Backend: {health.ready ? 'Ready' : 'Not ready'}</li>
              <li>MongoDB: {health.mongodb ? 'Connected' : 'Unavailable'}</li>
              <li>Cameras: {health.cameraCount ?? '—'}</li>
              <li>Recording: {health.enabled || health.recording?.enabled ? 'Enabled' : 'Disabled'}</li>
            </ul>
          ) : (
            <p className="text-sm text-gray-500">Unavailable</p>
          )}
        </Card>

        <Card className="sm:col-span-2">
          <div className="flex items-center gap-3 mb-2">
            <Radio size={18} className="text-purple-400" />
            <h3 className="font-semibold text-white">Media workers</h3>
          </div>
          {go2rtc ? (
            workers.length ? (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {workers.map((w) => (
                  <div key={w.workerId} className="rounded-md border border-gray-700 p-3 text-sm">
                    <p className="font-medium text-white">w{w.workerId}</p>
                    <p className={w.running ? 'text-green-400' : 'text-red-400'}>{w.running ? 'Running' : 'Stopped'}</p>
                    <p className="text-gray-400">Cameras: {w.assignedCameraCount ?? '—'}</p>
                    <p className="text-gray-400">Live streams: {w.liveStreamCount ?? '—'}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">
                {go2rtc.running ? 'Running' : 'Stopped'}
                {typeof go2rtc.cameraCount === 'number' ? ` · ${go2rtc.cameraCount} cameras` : ''}
              </p>
            )
          ) : (
            <p className="text-sm text-gray-500">Unavailable</p>
          )}
        </Card>
      </div>

      <Card>
        <div className="flex items-center gap-3 mb-3">
          <Activity size={18} className="text-blue-400" />
          <h3 className="font-semibold text-white">Recent activity</h3>
        </div>
        {!recent || recent.length === 0 ? (
          <p className="text-sm text-gray-500">No recent audit events.</p>
        ) : (
          <ul className="divide-y divide-gray-700 text-sm">
            {recent.map((item) => (
              <li key={item.id} className="py-2 flex flex-wrap gap-x-3 gap-y-1 text-gray-300">
                <span className="text-gray-500 w-40 shrink-0">{formatLocalDateTime(item.timestamp)}</span>
                <span className="font-medium text-white">{actorDisplayName(item)}</span>
                <span>{displayRole(item.actor_role)}</span>
                <span>{item.action}</span>
                <span className="text-gray-400">{item.resource_label || item.resource_id || ''}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
