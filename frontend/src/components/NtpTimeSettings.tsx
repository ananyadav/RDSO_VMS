import React, { useCallback, useEffect, useState } from 'react';
import { apiFetch, readJsonResponse } from '../lib/api';

interface SiteTimeSource {
  capable?: boolean;
  active?: boolean;
  mode?: string | null;
  guidance?: string;
}

interface SystemTimeStatus {
  utc_now?: string;
  app_timezone?: string;
  local_now?: string;
  recording_timestamps?: string;
  sync_state?: string;
  synchronized?: boolean;
  ntp?: {
    in_app_ntp_server?: boolean;
    sync_responsibility?: string;
    service?: string | null;
    service_running?: boolean;
    sync_state?: string;
    synchronized?: boolean;
    source?: string | null;
    ntp_servers?: string[];
    stratum?: number | string;
    last_sync?: string | null;
    site_time_source?: SiteTimeSource;
    guidance?: string;
    error?: string | null;
  };
}

function syncBadge(state?: string): { label: string; className: string } {
  switch (state) {
    case 'synchronized':
      return { label: 'Synchronized', className: 'bg-emerald-700 text-white' };
    case 'not_synchronized':
      return { label: 'Not synchronized', className: 'bg-amber-700 text-white' };
    case 'configuration_required':
      return { label: 'Configuration required', className: 'bg-orange-700 text-white' };
    case 'service_unavailable':
    default:
      return { label: 'Service unavailable', className: 'bg-red-800 text-white' };
  }
}

/**
 * RDSO 18.3.6 — VMS management UI for the host OS time service.
 * Does not run an in-app NTP protocol server.
 */
export default function NtpTimeSettings(): React.ReactElement {
  const [status, setStatus] = useState<SystemTimeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [peers, setPeers] = useState('');
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/system/time');
      const data = await readJsonResponse<SystemTimeStatus & { error?: string }>(res);
      if (!res.ok) {
        throw new Error(data?.error || res.statusText || 'Failed to load system time');
      }
      setStatus(data);
      setPeers((prev) => {
        if (prev.trim()) return prev;
        const servers = data?.ntp?.ntp_servers;
        return servers?.length ? servers.join(' ') : prev;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load system time');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(action: 'resync' | 'enable_site_ntp_server') {
    const confirmed = window.confirm(
      action === 'resync'
        ? 'Force an OS time resync now? This uses chrony/w32time on the VMS host.'
        : 'Explicitly configure this VMS host as a site NTP source via the OS time service? This will change host NTP settings.',
    );
    if (!confirmed) return;
    setBusy(true);
    setActionMsg(null);
    try {
      const res = await apiFetch('/api/system/time/actions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          confirm: true,
          ntp_peers: peers.trim() || undefined,
        }),
      });
      const data = await readJsonResponse<{
        result?: { ok?: boolean; error?: string; guidance?: string | string[] };
        status?: SystemTimeStatus;
        error?: string;
      }>(res);
      if (data.status) setStatus(data.status);
      if (!res.ok || data.result?.ok === false) {
        const g = data.result?.guidance;
        const guide = Array.isArray(g) ? g.join('\n') : g;
        setActionMsg(data.result?.error || data.error || guide || 'Action failed');
      } else {
        setActionMsg(action === 'resync' ? 'OS resync requested.' : 'Site NTP configuration applied / guidance returned.');
        await load();
      }
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  }

  const badge = syncBadge(status?.sync_state || status?.ntp?.sync_state);
  const site = status?.ntp?.site_time_source;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-6 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-xl font-bold text-white">Network time synchronization</h3>
          <p className="text-gray-400 mt-2 text-sm max-w-3xl">
            Recording timestamps use the VMS host OS clock (UTC). This panel manages and reports the
            real OS time service (chrony / systemd-timesyncd / Windows Time). The application does
            not implement a Python NTP server.
          </p>
        </div>
        <span className={`px-3 py-1 rounded text-xs font-semibold ${badge.className}`}>{badge.label}</span>
      </div>

      {loading && <p className="text-gray-400 text-sm">Loading system time…</p>}
      {error && <p className="text-red-400 text-sm">{error}</p>}
      {actionMsg && (
        <p className="text-sm text-amber-200 whitespace-pre-wrap border border-amber-800/50 rounded-md p-3 bg-amber-950/30">
          {actionMsg}
        </p>
      )}

      {status && (
        <>
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">UTC now (server)</dt>
              <dd className="text-white font-mono mt-1 break-all">{status.utc_now}</dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">APP_TIMEZONE</dt>
              <dd className="text-white font-mono mt-1">{status.app_timezone}</dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">Local now (APP_TIMEZONE)</dt>
              <dd className="text-white font-mono mt-1 break-all">{status.local_now}</dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">OS time service</dt>
              <dd className="text-white mt-1">
                {status.ntp?.service || '—'}
                {status.ntp?.service_running != null && (
                  <span className="text-gray-400"> ({status.ntp.service_running ? 'running' : 'not running'})</span>
                )}
              </dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">NTP source / peers</dt>
              <dd className="text-white font-mono mt-1 break-all">
                {status.ntp?.source || status.ntp?.ntp_servers?.join(', ') || '—'}
              </dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3">
              <dt className="text-gray-400">Last sync / stratum</dt>
              <dd className="text-white mt-1">
                {status.ntp?.last_sync || '—'}
                {status.ntp?.stratum != null && <span className="text-gray-400"> · stratum {status.ntp.stratum}</span>}
              </dd>
            </div>
            <div className="bg-gray-900/60 border border-gray-700 rounded-md p-3 md:col-span-2">
              <dt className="text-gray-400">Site time source (this VMS host)</dt>
              <dd className="text-white mt-1">
                {site?.active ? 'Active' : site?.capable ? 'Capable — configuration may be required' : 'Not capable with current OS service'}
                {site?.mode && <span className="text-gray-400"> · {site.mode}</span>}
              </dd>
            </div>
          </dl>

          {(status.ntp?.guidance || site?.guidance) && (
            <p className="text-gray-300 text-sm border border-gray-700 rounded-md p-3 bg-gray-900/40 whitespace-pre-wrap">
              {status.ntp?.guidance || site?.guidance}
            </p>
          )}

          <div className="border border-gray-700 rounded-md p-4 space-y-3 bg-gray-900/30">
            <h4 className="text-white font-semibold text-sm">Administrator actions (explicit confirm)</h4>
            <label className="block text-xs text-gray-400">
              Upstream NTP peers (optional, Windows enable / display)
              <input
                className="mt-1 w-full input-field bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-white"
                value={peers}
                onChange={(e) => setPeers(e.target.value)}
                placeholder="time.windows.com pool.ntp.org"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void runAction('resync')}
                className="px-3 py-2 text-sm rounded bg-blue-600 text-white disabled:opacity-50"
              >
                Force OS resync
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void runAction('enable_site_ntp_server')}
                className="px-3 py-2 text-sm rounded bg-indigo-700 text-white disabled:opacity-50"
              >
                Enable site NTP source (OS)
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void load()}
                className="px-3 py-2 text-sm rounded bg-gray-700 text-white disabled:opacity-50"
              >
                Refresh status
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
