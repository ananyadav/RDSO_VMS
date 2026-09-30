import React, { useCallback, useEffect, useState } from 'react';
import { Download, Loader2, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  downloadCccHealthCsv,
  fetchCccHealthReport,
  type CccHealthReport,
  type CccHealthRow,
} from '../lib/cccHealthReportsApi';

const PAGE = 50;

function statusClass(status: string): string {
  if (status === 'healthy') return 'text-emerald-700 dark:text-emerald-400';
  if (status === 'degraded') return 'text-amber-700 dark:text-amber-400';
  if (status === 'offline') return 'text-red-700 dark:text-red-400';
  return 'text-gray-500';
}

/** RDSO 18.6.14 — CCC Health Reports (software / non-GIS). */
export default function CccHealthReports(): React.ReactElement {
  const [data, setData] = useState<CccHealthReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [componentType, setComponentType] = useState('');
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [offset, setOffset] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rep = await fetchCccHealthReport({
        component_type: componentType || undefined,
        status: status || undefined,
        q: q.trim() || undefined,
        limit: PAGE,
        offset,
      });
      setData(rep);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Health report failed');
    } finally {
      setLoading(false);
    }
  }, [componentType, status, q, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const onExport = async () => {
    try {
      await downloadCccHealthCsv({
        component_type: componentType || undefined,
        status: status || undefined,
        q: q.trim() || undefined,
      });
      toast.success('Health CSV exported (audited)');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    }
  };

  const items: CccHealthRow[] = data?.items || [];
  const types = data?.component_types || [
    'camera_fleet',
    'camera',
    'vms_server',
    'recording_server',
    'go2rtc_worker',
    'storage',
    'backend',
    'recording_subsystem',
    'ntp',
    'failure',
  ];

  return (
    <div className="flex flex-col gap-3 h-full min-h-0" data-testid="ccc-health-reports" data-rdso="18.6.14">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Health Reports</h2>
          <p className="text-[10px] text-gray-500">
            Real VMS probes only — no fabricated metrics. GIS / Site Maps not included (pending).
          </p>
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs border border-gray-300 dark:border-gray-600"
            onClick={() => void load()}
          >
            <RefreshCw size={12} /> Refresh
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs bg-emerald-700 text-white"
            onClick={() => void onExport()}
          >
            <Download size={12} /> CSV
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <select
          value={componentType}
          onChange={(e) => {
            setOffset(0);
            setComponentType(e.target.value);
          }}
          className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        >
          <option value="">All components</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          value={status}
          onChange={(e) => {
            setOffset(0);
            setStatus(e.target.value);
          }}
          className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        >
          <option value="">Any status</option>
          <option value="healthy">healthy</option>
          <option value="degraded">degraded</option>
          <option value="offline">offline</option>
          <option value="unknown">unknown</option>
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              setOffset(0);
              void load();
            }
          }}
          placeholder="Filter text…"
          className="flex-1 min-w-[8rem] px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
        <button
          type="button"
          className="px-2 py-1 rounded bg-gray-800 text-white"
          onClick={() => {
            setOffset(0);
            void load();
          }}
        >
          Apply
        </button>
      </div>

      {data?.summary ? (
        <div className="flex flex-wrap gap-3 text-[11px] text-gray-600 dark:text-gray-300">
          <span>healthy: {data.summary.healthy ?? 0}</span>
          <span>degraded: {data.summary.degraded ?? 0}</span>
          <span>offline: {data.summary.offline ?? 0}</span>
          <span>unknown: {data.summary.unknown ?? 0}</span>
          <span className="text-gray-400">GIS: pending</span>
        </div>
      ) : null}

      {loading && !data ? (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
          <Loader2 className="animate-spin" size={16} /> Loading health…
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-auto border border-gray-200 dark:border-gray-700 rounded">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 dark:bg-gray-950 sticky top-0">
              <tr className="text-left">
                <th className="p-2">Component</th>
                <th className="p-2">Type</th>
                <th className="p-2">Status</th>
                <th className="p-2">Last check</th>
                <th className="p-2">Message</th>
                <th className="p-2">Location / server</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
              {items.map((row, i) => (
                <tr key={`${row.component_type}-${row.component}-${i}`}>
                  <td className="p-2 font-medium">{row.component}</td>
                  <td className="p-2 text-gray-500">{row.component_type}</td>
                  <td className={`p-2 font-semibold ${statusClass(row.status)}`}>{row.status}</td>
                  <td className="p-2 text-gray-500 whitespace-nowrap">{row.last_check || '—'}</td>
                  <td className="p-2 max-w-xs truncate" title={row.message}>
                    {row.message || '—'}
                  </td>
                  <td className="p-2 text-gray-500">
                    {[row.location, row.server_id].filter(Boolean).join(' · ') || '—'}
                  </td>
                </tr>
              ))}
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-6 text-center text-gray-500">
                    No health rows for this filter
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center gap-2 text-xs">
        <button
          type="button"
          disabled={offset <= 0}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
        >
          Prev
        </button>
        <span>
          {offset + 1}–{offset + items.length} / {data?.total ?? 0}
        </span>
        <button
          type="button"
          disabled={!data || offset + PAGE >= (data.total || 0)}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
