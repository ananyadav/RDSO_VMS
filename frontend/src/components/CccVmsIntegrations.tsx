import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';
import {
  createVmsIntegration,
  deleteVmsIntegration,
  fetchVmsIntegrations,
  rotateVmsIntegrationSecret,
  testVmsIntegration,
  updateVmsIntegration,
  type VmsIntegration,
} from '../lib/cccVmsIntegrationsApi';

/** Admin: third-party VMS integration sources (generic REST — no bundled vendor SDK). */
export default function CccVmsIntegrations(): React.ReactElement {
  const user = authService.getCurrentUser();
  const canAdmin = isOpsAdminUser(user);
  const [items, setItems] = useState<VmsIntegration[]>([]);
  const [sid, setSid] = useState('');
  const [label, setLabel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [credential, setCredential] = useState('');

  const load = useCallback(async () => {
    if (!canAdmin) return;
    try {
      const data = await fetchVmsIntegrations();
      setItems(data.items || []);
    } catch {
      /* admin only */
    }
  }, [canAdmin]);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!sid.trim() || !baseUrl.trim()) return;
    try {
      const res = await createVmsIntegration({
        source_id: sid.trim().toLowerCase(),
        label: label.trim() || sid.trim(),
        base_url: baseUrl.trim(),
        credential: credential.trim() || undefined,
        enabled: false,
        capabilities: { live: true, playback: false, events: true, health: true },
      });
      toast.success('Integration saved (disabled until enabled)');
      if (res.integration.integration_secret) {
        toast('Alert webhook secret shown once — copy from integration detail', { duration: 8000 });
      }
      setSid('');
      setLabel('');
      setBaseUrl('');
      setCredential('');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Create failed');
    }
  };

  const toggle = async (it: VmsIntegration) => {
    try {
      await updateVmsIntegration(it.source_id, { enabled: !it.enabled });
      toast.success(it.enabled ? 'Disabled' : 'Enabled');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Update failed');
    }
  };

  const test = async (sourceId: string) => {
    try {
      const res = await testVmsIntegration(sourceId);
      if (res.ok) toast.success(`Connection OK (${res.status || 'healthy'})`);
      else toast.error(`Test failed: ${res.status || 'offline'}`);
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Test failed');
    }
  };

  const rotate = async (sourceId: string) => {
    try {
      const res = await rotateVmsIntegrationSecret(sourceId);
      if (res.integration.integration_secret) {
        toast.success('New alert ingest secret — shown once in API response');
      }
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Rotate failed');
    }
  };

  const remove = async (sourceId: string) => {
    if (!window.confirm(`Delete integration ${sourceId}?`)) return;
    try {
      await deleteVmsIntegration(sourceId);
      toast.success('Deleted');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  if (!canAdmin) {
    return (
      <p className="text-xs text-gray-500" data-testid="ccc-vms-integrations">
        Admin required to manage external VMS integrations.
      </p>
    );
  }

  return (
    <div className="space-y-2 text-xs" data-testid="ccc-vms-integrations" data-rdso="18.6.17.3">
      <h3 className="font-semibold text-sm">External VMS integrations</h3>
      <p className="text-gray-500">
        Generic REST adapter only — purchaser maps endpoints. No named vendor SDK bundled. Path: CCC →
        adapter → external VMS (never direct camera).
      </p>
      <ul className="border rounded divide-y dark:divide-gray-700 max-h-40 overflow-auto">
        {items.map((it) => (
          <li key={it.source_id} className="p-2 space-y-1">
            <div className="font-medium">
              {it.label} · {it.source_id}{' '}
              <span className={it.enabled ? 'text-emerald-600' : 'text-gray-500'}>
                {it.enabled ? 'enabled' : 'disabled'}
              </span>
            </div>
            <div className="text-gray-500 truncate">{it.base_url}</div>
            <div className="text-gray-500">
              health={it.health || 'unknown'} · live={String(it.capabilities?.live)} · events=
              {String(it.capabilities?.events)}
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="underline" onClick={() => void toggle(it)}>
                {it.enabled ? 'Disable' : 'Enable'}
              </button>
              <button type="button" className="underline" onClick={() => void test(it.source_id)}>
                Test
              </button>
              <button type="button" className="underline" onClick={() => void rotate(it.source_id)}>
                Rotate alert secret
              </button>
              <button type="button" className="underline text-red-700" onClick={() => void remove(it.source_id)}>
                Delete
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 ? (
          <li className="p-3 text-gray-500 text-center">No external integrations</li>
        ) : null}
      </ul>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
        <input
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          placeholder="source_id (e.g. purchaser-vms)"
          className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Label"
          className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://external-vms.example.com"
          className="md:col-span-2 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
        <input
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          placeholder="API credential (never shown again)"
          type="password"
          className="md:col-span-2 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
      </div>
      <button type="button" className="px-2 py-1 rounded bg-emerald-700 text-white" onClick={() => void add()}>
        Add integration (starts disabled)
      </button>
    </div>
  );
}
