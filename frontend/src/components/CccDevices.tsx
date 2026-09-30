import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';
import {
  type CccDevice,
  type CccPreemptionPolicy,
  createCccDevice,
  deleteCccDevice,
  fetchCccDevices,
  fetchCccPreemptionPolicy,
  saveCccPreemptionPolicy,
  updateCccDevice,
} from '../lib/cccDevicesApi';

const PAGE = 50;

export default function CccDevicesPanel(): React.ReactElement {
  const user = authService.getCurrentUser();
  const canAdmin = isOpsAdminUser(user);

  const [items, setItems] = useState<CccDevice[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState('');
  const [q, setQ] = useState('');
  const [secretOnce, setSecretOnce] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [dtype, setDtype] = useState('external_sensor');
  const [location, setLocation] = useState('');
  const [cameraIds, setCameraIds] = useState('');

  const [policy, setPolicy] = useState<CccPreemptionPolicy | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCccDevices({
        type: typeFilter || undefined,
        q: q.trim() || undefined,
        limit: PAGE,
        offset,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Load failed');
    } finally {
      setLoading(false);
    }
  }, [typeFilter, q, offset]);

  const loadPolicy = useCallback(async () => {
    try {
      setPolicy(await fetchCccPreemptionPolicy());
    } catch {
      setPolicy(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadPolicy();
  }, [loadPolicy]);

  const addDevice = async () => {
    if (!name.trim()) return toast.error('Name required');
    try {
      const cams = cameraIds
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      const device = await createCccDevice({
        name: name.trim(),
        type: dtype,
        location: location.trim(),
        linked_camera_ids: cams,
        generate_secret: dtype === 'external_sensor',
      });
      if (device.integration_secret) setSecretOnce(device.integration_secret);
      toast.success('Device registered (no streams opened)');
      setName('');
      setLocation('');
      setCameraIds('');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Create failed');
    }
  };

  const toggleEnabled = async (d: CccDevice) => {
    try {
      await updateCccDevice(d.id, { enabled: !d.enabled });
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Update failed');
    }
  };

  const remove = async (d: CccDevice) => {
    if (!window.confirm(`Remove device ${d.name}?`)) return;
    try {
      await deleteCccDevice(d.id);
      toast.success('Removed');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  const savePolicy = async () => {
    if (!policy) return;
    try {
      const saved = await saveCccPreemptionPolicy({
        enabled: policy.enabled,
        equal_priority: policy.equal_priority,
        min_priority_to_preempt: policy.min_priority_to_preempt,
        scopes: policy.scopes,
      });
      setPolicy(saved);
      toast.success('Pre-emption policy saved');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed');
    }
  };

  return (
    <div className="space-y-4 text-xs" data-testid="ccc-devices" data-rdso="18.6.22.3,18.6.22.12,18.6.22.15">
      <p className="text-gray-600 dark:text-gray-300">
        Centralized CCC devices/sensors — camera, VMS, camera digital input, generic external sensor.
        Alerts enter the existing Event Log → Dashboard → Hot Screen → Incident path. No fake vendors;
        registration does not open streams.
      </p>

      {secretOnce ? (
        <div className="rounded border border-amber-500 bg-amber-50 dark:bg-amber-950/30 p-2">
          <div className="font-semibold">Integration secret (shown once)</div>
          <code className="break-all">{secretOnce}</code>
          <button type="button" className="ml-2 underline" onClick={() => setSecretOnce(null)}>
            Dismiss
          </button>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2 items-end">
        <label className="flex flex-col gap-0.5">
          Type
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value);
              setOffset(0);
            }}
            className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          >
            <option value="">All</option>
            <option value="camera">camera</option>
            <option value="vms">vms</option>
            <option value="camera_digital_input">camera_digital_input</option>
            <option value="external_sensor">external_sensor</option>
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          Search
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          />
        </label>
        <button type="button" className="px-2 py-1 rounded bg-gray-800 text-white" onClick={() => void load()}>
          Apply
        </button>
        <span className="text-gray-500">{total} devices · no hard cap</span>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500 py-6 justify-center">
          <Loader2 className="animate-spin" size={16} /> Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="border border-dashed rounded p-6 text-center text-gray-500">No devices registered</div>
      ) : (
        <div className="overflow-auto border rounded max-h-[40vh]">
          <table className="min-w-full text-left">
            <thead className="bg-gray-50 dark:bg-gray-950 sticky top-0">
              <tr>
                <th className="px-2 py-1">Name</th>
                <th className="px-2 py-1">Type</th>
                <th className="px-2 py-1">Location</th>
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Last seen</th>
                <th className="px-2 py-1">Alerts</th>
                <th className="px-2 py-1">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr key={d.id} className="border-t border-gray-100 dark:border-gray-800">
                  <td className="px-2 py-1 font-medium">{d.name}</td>
                  <td className="px-2 py-1">{d.type}</td>
                  <td className="px-2 py-1">{d.location || '—'}</td>
                  <td className="px-2 py-1">
                    {d.enabled ? d.status : 'disabled'} / {d.health}
                  </td>
                  <td className="px-2 py-1 whitespace-nowrap">{d.last_seen || '—'}</td>
                  <td className="px-2 py-1">{d.active_alert_count || 0}</td>
                  <td className="px-2 py-1 space-x-2">
                    {d.last_event_id ? (
                      <Link className="underline" to="/ccc?tab=events">
                        Event
                      </Link>
                    ) : null}
                    {canAdmin ? (
                      <>
                        <button type="button" className="underline" onClick={() => void toggleEnabled(d)}>
                          {d.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button type="button" className="underline text-red-700" onClick={() => void remove(d)}>
                          Remove
                        </button>
                      </>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={offset <= 0}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
        >
          Prev
        </button>
        <button
          type="button"
          disabled={offset + PAGE >= total}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </button>
      </div>

      {canAdmin ? (
        <div className="border rounded p-2 space-y-2 bg-gray-50 dark:bg-gray-950/40">
          <div className="font-semibold text-sm">Add device (Admin)</div>
          <div className="flex flex-wrap gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
            <select
              value={dtype}
              onChange={(e) => setDtype(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            >
              <option value="external_sensor">external_sensor</option>
              <option value="camera">camera</option>
              <option value="vms">vms</option>
              <option value="camera_digital_input">camera_digital_input</option>
            </select>
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Location"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
            <input
              value={cameraIds}
              onChange={(e) => setCameraIds(e.target.value)}
              placeholder="Linked camera ids (comma)"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent min-w-[12rem]"
            />
            <button type="button" className="px-3 py-1 rounded bg-emerald-700 text-white" onClick={() => void addDevice()}>
              Add
            </button>
          </div>
          <p className="text-gray-500">
            Ingest: <code>POST /api/ccc/devices/&#123;id&#125;/ingest</code> with Bearer secret → existing events pipeline.
          </p>
        </div>
      ) : null}

      {policy ? (
        <div className="border rounded p-2 space-y-2">
          <div className="font-semibold text-sm">Pre-emption policy (priority 1–5, not RBAC)</div>
          <p className="text-gray-500">{policy.note}</p>
          <div className="flex flex-wrap gap-3 items-center">
            <label className="inline-flex items-center gap-1">
              <input
                type="checkbox"
                checked={policy.enabled}
                disabled={!canAdmin}
                onChange={(e) => setPolicy({ ...policy, enabled: e.target.checked })}
              />
              Enabled
            </label>
            <label className="flex items-center gap-1">
              Equal priority
              <select
                value={policy.equal_priority}
                disabled={!canAdmin}
                onChange={(e) => setPolicy({ ...policy, equal_priority: e.target.value })}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              >
                <option value="incoming_wins">incoming_wins</option>
                <option value="holder_wins">holder_wins</option>
              </select>
            </label>
            <label className="flex items-center gap-1">
              Min priority to preempt
              <input
                type="number"
                min={1}
                max={5}
                disabled={!canAdmin}
                value={policy.min_priority_to_preempt}
                onChange={(e) =>
                  setPolicy({ ...policy, min_priority_to_preempt: Number(e.target.value) || 1 })
                }
                className="w-14 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
            {canAdmin ? (
              <button type="button" className="px-3 py-1 rounded bg-emerald-700 text-white" onClick={() => void savePolicy()}>
                Save policy
              </button>
            ) : null}
          </div>
          <div className="text-gray-500">
            Live display auto-steal still uses alarm.priority ≥ user.priority when live_display scope is on.
            Users/RBAC:{' '}
            <Link className="underline" to="/user-management">
              User management
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
