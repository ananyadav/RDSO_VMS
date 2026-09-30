import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Loader2, ExternalLink } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { apiFetch, readJsonResponse } from '../lib/api';

export type SettingsCatalogItem = {
  key: string;
  name: string;
  value: unknown;
  scope: string;
  source: string;
  editable: boolean;
  restart_required: boolean;
  validation?: string | null;
  notes?: string | null;
  manage_path?: string | null;
};

type CatalogResponse = {
  items: SettingsCatalogItem[];
  total: number;
  scopes?: string[];
};

const SCOPE_LABELS: Record<string, string> = {
  vms_server: 'VMS / Server',
  recording_server: 'Recording Server',
  camera: 'Camera',
  client: 'Client / Application',
};

function formatValue(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

export default function SystemSettings(): React.ReactElement {
  const [items, setItems] = useState<SettingsCatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState('');
  const [editableOnly, setEditableOnly] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams();
      if (scope) q.set('scope', scope);
      if (editableOnly) q.set('editable', 'true');
      const suffix = q.toString() ? `?${q.toString()}` : '';
      const response = await apiFetch(`/api/settings/catalog${suffix}`);
      const data = await readJsonResponse<CatalogResponse & { error?: string }>(response);
      if (!response.ok) {
        throw new Error(data?.error || response.statusText || 'Failed to load catalog');
      }
      setItems(data.items || []);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load settings catalog';
      setError(message);
      setItems([]);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [editableOnly, scope]);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const map = new Map<string, SettingsCatalogItem[]>();
    for (const item of items) {
      const list = map.get(item.scope) || [];
      list.push(item);
      map.set(item.scope, list);
    }
    return map;
  }, [items]);

  return (
    <div className="h-full flex flex-col p-3 sm:p-4 gap-3 min-h-0">
      <PageHeader
        title="System Settings"
        subtitle="Catalog and manage VMS, recording, camera, and client configuration"
      />

      <div className="flex flex-wrap gap-2 text-sm">
        <Link className="px-3 py-1.5 rounded bg-gray-800 text-white hover:bg-gray-700" to="/storage">
          Storage / Recording
        </Link>
        <Link className="px-3 py-1.5 rounded bg-gray-800 text-white hover:bg-gray-700" to="/network-settings?tab=NTP">
          Network / NTP
        </Link>
        <Link className="px-3 py-1.5 rounded bg-gray-800 text-white hover:bg-gray-700" to="/camera-management">
          Cameras
        </Link>
      </div>

      <div className="flex flex-wrap gap-3 items-end bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-3">
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          <span>Scope</span>
          <select className="select-style" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">All scopes</option>
            {Object.entries(SCOPE_LABELS).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200 pb-1">
          <input
            type="checkbox"
            checked={editableOnly}
            onChange={(e) => setEditableOnly(e.target.checked)}
          />
          Editable only
        </label>
        <button
          type="button"
          onClick={() => void load()}
          className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 text-sm"
        >
          Refresh
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-auto space-y-4">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <Loader2 className="animate-spin mr-2" size={20} /> Loading catalog…
          </div>
        ) : error ? (
          <div className="py-12 text-center text-gray-500">{error}</div>
        ) : items.length === 0 ? (
          <div className="py-12 text-center text-gray-500">No settings match these filters.</div>
        ) : (
          Array.from(grouped.entries()).map(([sc, rows]) => (
            <section
              key={sc}
              className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
            >
              <header className="px-4 py-2 border-b border-gray-200 dark:border-gray-700 font-semibold text-gray-900 dark:text-white">
                {SCOPE_LABELS[sc] || sc}
              </header>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left min-w-[48rem]">
                  <thead className="bg-gray-50 dark:bg-gray-700/40 text-xs uppercase text-gray-500">
                    <tr>
                      <th className="px-3 py-2">Setting</th>
                      <th className="px-3 py-2">Value</th>
                      <th className="px-3 py-2">Source</th>
                      <th className="px-3 py-2">Editable</th>
                      <th className="px-3 py-2">Restart</th>
                      <th className="px-3 py-2">Manage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.key} className="border-t border-gray-100 dark:border-gray-700 align-top">
                        <td className="px-3 py-2">
                          <div className="font-medium text-gray-900 dark:text-white">{row.name}</div>
                          <div className="text-xs text-gray-500 font-mono">{row.key}</div>
                          {row.validation ? (
                            <div className="text-xs text-gray-400 mt-0.5">Validation: {row.validation}</div>
                          ) : null}
                          {row.notes ? (
                            <div className="text-xs text-gray-400 mt-0.5 max-w-md">{row.notes}</div>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs break-all max-w-xs">{formatValue(row.value)}</td>
                        <td className="px-3 py-2 text-xs text-gray-600 dark:text-gray-300 max-w-[12rem]">{row.source}</td>
                        <td className="px-3 py-2">{row.editable ? 'Yes' : 'Read-only'}</td>
                        <td className="px-3 py-2">
                          {row.restart_required ? (
                            <span className="text-amber-700 dark:text-amber-300 text-xs font-semibold">Required</span>
                          ) : (
                            'No'
                          )}
                        </td>
                        <td className="px-3 py-2">
                          {row.manage_path ? (
                            <Link
                              to={row.manage_path}
                              className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:underline"
                            >
                              Open <ExternalLink size={12} />
                            </Link>
                          ) : (
                            '—'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  );
}
