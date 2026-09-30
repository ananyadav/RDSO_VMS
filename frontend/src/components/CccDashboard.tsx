import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Settings2 } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  type CccDashWidget,
  type CccDashboard,
  fetchCccDashboard,
  saveCccDashboardPrefs,
} from '../lib/cccDashboardApi';

function MetricCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div className="rounded border border-gray-200 dark:border-gray-700 p-2.5 bg-gray-50 dark:bg-gray-950/40">
      <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">{title}</div>
      <div className="text-xs text-gray-800 dark:text-gray-100 space-y-0.5">{children}</div>
    </div>
  );
}

function num(v: unknown): string {
  if (v == null) return '—';
  return String(v);
}

export default function CccDashboardPanel({
  onOpenHotScreen,
}: {
  onOpenHotScreen?: () => void;
}): React.ReactElement {
  const [data, setData] = useState<CccDashboard | null>(null);
  const [widgets, setWidgets] = useState<CccDashWidget[]>([]);
  const [loading, setLoading] = useState(true);
  const [editLayout, setEditLayout] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const dash = await fetchCccDashboard();
      setData(dash);
      setWidgets(dash.prefs?.widgets || []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Dashboard failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const ms = Math.max(10, data?.poll_seconds_suggested || 15) * 1000;
    const t = window.setInterval(() => void load(), ms);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll interval from first load is fine
  }, [load]);

  const enabledOrdered = useMemo(
    () =>
      [...widgets]
        .filter((w) => w.enabled)
        .sort((a, b) => a.order - b.order),
    [widgets],
  );

  const toggleWidget = (id: string) => {
    setWidgets((prev) =>
      prev.map((w) => (w.id === id ? { ...w, enabled: !w.enabled } : w)),
    );
  };

  const moveWidget = (id: string, dir: -1 | 1) => {
    setWidgets((prev) => {
      const sorted = [...prev].sort((a, b) => a.order - b.order);
      const idx = sorted.findIndex((w) => w.id === id);
      const j = idx + dir;
      if (idx < 0 || j < 0 || j >= sorted.length) return prev;
      const a = sorted[idx];
      const b = sorted[j];
      const next = sorted.map((w) => {
        if (w.id === a.id) return { ...w, order: b.order };
        if (w.id === b.id) return { ...w, order: a.order };
        return w;
      });
      return next.sort((x, y) => x.order - y.order).map((w, i) => ({ ...w, order: i }));
    });
  };

  const saveLayout = async () => {
    setSaving(true);
    try {
      const res = await saveCccDashboardPrefs(widgets);
      setWidgets(res.prefs.widgets);
      toast.success('Dashboard layout saved (this user only)');
      setEditLayout(false);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
        <Loader2 className="animate-spin" size={16} /> Loading dashboard…
      </div>
    );
  }

  if (!data) {
    return <p className="text-sm text-gray-500">No dashboard data</p>;
  }

  const renderWidget = (id: string) => {
    switch (id) {
      case 'cameras':
        return (
          <MetricCard key={id} title="Cameras">
            <div className="text-lg font-semibold">{data.cameras.total}</div>
            <div>
              Online: {num(data.cameras.online)} · Offline: {num(data.cameras.offline)}
            </div>
            {!data.cameras.online_status_known && data.cameras.note ? (
              <div className="text-[10px] text-amber-700 dark:text-amber-400">{data.cameras.note}</div>
            ) : null}
          </MetricCard>
        );
      case 'events':
        return (
          <MetricCard key={id} title="Events">
            <div>
              Open: <strong>{data.events.open}</strong>
            </div>
            <div>
              Critical: <strong className="text-red-600">{data.events.critical}</strong>
            </div>
          </MetricCard>
        );
      case 'incidents':
        return (
          <MetricCard key={id} title="Incidents">
            <div>Active: {data.incidents.open_active}</div>
            <div>Escalated: {data.incidents.escalated}</div>
            <div>
              Critical open: <strong className="text-red-600">{data.incidents.critical_open}</strong>
            </div>
          </MetricCard>
        );
      case 'priority':
        return (
          <MetricCard key={id} title="Priority / status">
            <div className="flex flex-wrap gap-1">
              {Object.entries(data.incidents.by_priority || {}).map(([p, n]) => (
                <span key={p} className="px-1.5 py-0.5 rounded bg-white dark:bg-gray-900 border text-[10px]">
                  P{p}: {n}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-1 mt-1">
              {Object.entries(data.incidents.by_status || {}).map(([s, n]) =>
                n > 0 ? (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-white dark:bg-gray-900 border text-[10px]">
                    {s}: {n}
                  </span>
                ) : null,
              )}
            </div>
          </MetricCard>
        );
      case 'health': {
        const rec = (data.health.recording || {}) as Record<string, unknown>;
        const ha = (data.health.vms_ha || {}) as Record<string, unknown>;
        return (
          <MetricCard key={id} title="VMS / recording health">
            <div>Recording: {String(rec.status ?? rec.ok ?? 'see status')}</div>
            <div>VMS HA: {String(ha.role ?? ha.status ?? 'n/a')}</div>
            <Link className="underline text-emerald-700" to="/system-status">
              System status
            </Link>
          </MetricCard>
        );
      }
      case 'storage':
        return (
          <MetricCard key={id} title="Storage">
            <div>Status: {String(data.storage.status ?? (data.storage.ok ? 'ok' : 'error'))}</div>
            <div>
              Free: {num(data.storage.free_percent)}% · Used: {num(data.storage.used_percent)}%
            </div>
            <Link className="underline text-emerald-700" to="/storage">
              Storage
            </Link>
          </MetricCard>
        );
      case 'recent':
        return (
          <MetricCard key={id} title="Recent activity">
            <ul className="space-y-1 max-h-28 overflow-auto">
              {(data.recent.incidents || []).slice(0, 5).map((inc) => (
                <li key={String(inc.id)} className="truncate">
                  [I] {String(inc.title)} · {String(inc.status)} · P{String(inc.priority)}
                </li>
              ))}
              {(data.recent.events || []).slice(0, 3).map((ev) => (
                <li key={String(ev.id)} className="truncate text-gray-500">
                  [E] {String(ev.title || ev.id)}
                </li>
              ))}
            </ul>
          </MetricCard>
        );
      case 'compliance':
        return (
          <MetricCard key={id} title="SOP compliance">
            <div>Pending: {num(data.compliance.pending)}</div>
            <div className="text-amber-700 dark:text-amber-400">
              Overdue: {num(data.compliance.overdue)}
            </div>
            <div>Complete: {num(data.compliance.complete)}</div>
          </MetricCard>
        );
      case 'hot_screen':
        return (
          <MetricCard key={id} title="Hot Screen preview">
            {(data.hot_screen_preview || []).length === 0 ? (
              <div className="text-gray-500">No critical situations</div>
            ) : (
              <ul className="space-y-1">
                {data.hot_screen_preview.map((h) => (
                  <li key={h.incident_id} className="truncate">
                    P{h.priority} · {h.title}
                  </li>
                ))}
              </ul>
            )}
            {onOpenHotScreen ? (
              <button type="button" className="underline text-red-700 mt-1" onClick={onOpenHotScreen}>
                Open Hot Screen
              </button>
            ) : null}
          </MetricCard>
        );
      default:
        return null;
    }
  };

  return (
    <div className="space-y-3" data-testid="ccc-dashboard" data-rdso="18.6.5,18.6.6,18.6.16.1">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-600 dark:text-gray-300">
          Live CCC metrics from VMS/Mongo — no fabricated statistics.
          {data.fake_statistics === false ? ' · fake_statistics=false' : null}
        </p>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600"
          onClick={() => setEditLayout((v) => !v)}
        >
          <Settings2 size={12} /> Customize layout
        </button>
      </div>

      {editLayout ? (
        <div className="rounded border border-dashed border-emerald-600/50 p-2 text-xs space-y-2 bg-emerald-50/40 dark:bg-emerald-950/20">
          <p className="text-gray-600 dark:text-gray-300">
            Per-user layout only — saving does not change other operators&apos; dashboards.
          </p>
          <ul className="space-y-1">
            {[...widgets]
              .sort((a, b) => a.order - b.order)
              .map((w) => (
                <li key={w.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={w.enabled}
                    onChange={() => toggleWidget(w.id)}
                  />
                  <span className="flex-1">{w.label}</span>
                  <button type="button" className="px-1 border rounded" onClick={() => moveWidget(w.id, -1)}>
                    ↑
                  </button>
                  <button type="button" className="px-1 border rounded" onClick={() => moveWidget(w.id, 1)}>
                    ↓
                  </button>
                </li>
              ))}
          </ul>
          <button
            type="button"
            disabled={saving}
            className="px-3 py-1.5 rounded bg-emerald-700 text-white disabled:opacity-50"
            onClick={() => void saveLayout()}
          >
            {saving ? 'Saving…' : 'Save my layout'}
          </button>
        </div>
      ) : null}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {enabledOrdered.map((w) => renderWidget(w.id))}
      </div>

      <div className="flex flex-wrap gap-3 text-xs">
        <Link className="underline" to="/ccc?tab=incidents">
          Incidents
        </Link>
        <Link className="underline" to="/ccc?tab=events">
          Event log
        </Link>
        <Link className="underline" to="/live">
          Live
        </Link>
        <Link className="underline" to="/user-management">
          Users / RBAC
        </Link>
      </div>
    </div>
  );
}
