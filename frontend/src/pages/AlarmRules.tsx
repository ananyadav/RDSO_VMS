import React, { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Edit, Plus, Trash2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import AlarmRuleModal, { type CameraOption } from '../components/AlarmRuleModal';
import ConfirmModal from '../components/control-center/ConfirmModal';
import {
  actionLabel,
  severityBadgeClass,
  severityLabel,
  triggerLabel,
} from '../lib/alarmRuleLabels';
import { formValuesToPayload } from '../lib/alarmRuleForm';
import {
  AlarmRulesRequestError,
  createAlarmRule,
  deleteAlarmRule,
  listAlarmRules,
  updateAlarmRule,
  type AlarmRule,
} from '../lib/alarmRulesApi';
import { apiFetch, cameraQuery, readJsonResponse } from '../lib/api';
import {
  useUrlHydration,
  useUrlSync,
  initialStringParam,
  paramFlag,
} from '../hooks/useUrlSearchState';

interface ConfiguredCamera {
  _id: string;
  name?: string;
  display_name?: string;
  ip_address?: string;
  camera_uid?: string;
}

function formatLastTriggered(rule: AlarmRule): string {
  const ts = rule.runtime?.last_triggered_at;
  if (!ts) return 'Never';
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function cameraLabel(cam: ConfiguredCamera): string {
  return (
    (cam.display_name || cam.name || cam.camera_uid || '').trim() ||
    cam.ip_address ||
    cam._id
  );
}

export default function AlarmRules(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [rules, setRules] = useState<AlarmRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cameras, setCameras] = useState<ConfiguredCamera[]>([]);
  const [camerasLoading, setCamerasLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(() =>
    paramFlag(initialParams.current?.get('add') ?? null, false),
  );
  const [editingRule, setEditingRule] = useState<AlarmRule | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AlarmRule | null>(null);

  const cameraById = useMemo(() => {
    const map = new Map<string, ConfiguredCamera>();
    for (const cam of cameras) {
      map.set(cam._id, cam);
    }
    return map;
  }, [cameras]);

  const cameraOptions: CameraOption[] = useMemo(
    () =>
      cameras.map((cam) => ({
        id: cam._id,
        label: cameraLabel(cam),
        ip_address: cam.ip_address,
      })),
    [cameras],
  );

  const fetchRules = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listAlarmRules({ limit: 200 });
      setRules(data.items);
    } catch (err) {
      const message =
        err instanceof AlarmRulesRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to load alarm rules';
      setLoadError(message);
      setRules([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCameras = useCallback(async () => {
    setCamerasLoading(true);
    try {
      const response = await apiFetch(
        `/api/cameras/configured${cameraQuery({ includeInactive: 'true' })}`,
      );
      const data = await readJsonResponse<{ items?: ConfiguredCamera[] } | ConfiguredCamera[]>(
        response,
      );
      const items = Array.isArray(data) ? data : data.items ?? [];
      setCameras(items);
    } catch {
      toast.error('Failed to load cameras');
      setCameras([]);
    } finally {
      setCamerasLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRules();
    void fetchCameras();
  }, [fetchRules, fetchCameras]);

  useEffect(() => {
    const editId = initialStringParam(initialParams, 'edit');
    if (editId && rules.length > 0 && !editingRule) {
      const match = rules.find((r) => r.id === editId);
      if (match) setEditingRule(match);
    }
    markHydrated();
  }, [rules, editingRule, initialParams, markHydrated]);

  const urlValues = useMemo(
    () => ({
      add: isCreateOpen ? '1' : null,
      edit: editingRule?.id ?? null,
    }),
    [isCreateOpen, editingRule],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  const resolveCameraName = (cameraId: string): string => {
    const cam = cameraById.get(cameraId);
    if (!cam) return cameraId;
    const label = cameraLabel(cam);
    return cam.ip_address ? `${label} (${cam.ip_address})` : label;
  };

  const handleCreate = async (payload: ReturnType<typeof formValuesToPayload>) => {
    setSaving(true);
    try {
      const created = await createAlarmRule(payload);
      toast.success('Alarm rule created');
      setRules((prev) => [created, ...prev.filter((r) => r.id !== created.id)]);
      setIsCreateOpen(false);
    } catch (err) {
      toast.error(err instanceof AlarmRulesRequestError ? err.message : 'Failed to create rule');
      throw err;
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = async (payload: ReturnType<typeof formValuesToPayload>) => {
    if (!editingRule) return;
    setSaving(true);
    try {
      const updated = await updateAlarmRule(editingRule.id, payload);
      toast.success('Alarm rule updated');
      setRules((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      setEditingRule(null);
    } catch (err) {
      toast.error(err instanceof AlarmRulesRequestError ? err.message : 'Failed to update rule');
      throw err;
    } finally {
      setSaving(false);
    }
  };

  const handleToggleEnabled = async (rule: AlarmRule) => {
    const next = !rule.enabled;
    try {
      const updated = await updateAlarmRule(rule.id, { enabled: next });
      setRules((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      toast.success(next ? 'Rule enabled' : 'Rule disabled');
    } catch (err) {
      toast.error(err instanceof AlarmRulesRequestError ? err.message : 'Failed to update rule');
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteAlarmRule(deleteTarget.id);
      setRules((prev) => prev.filter((r) => r.id !== deleteTarget.id));
      toast.success('Alarm rule deleted');
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof AlarmRulesRequestError ? err.message : 'Failed to delete rule');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Alarm Rules"
        subtitle="Configure when confirmed camera signal loss creates events and notifications"
        rightContent={
          <button
            type="button"
            onClick={() => setIsCreateOpen(true)}
            className="btn-primary flex items-center w-auto"
          >
            <Plus size={18} className="mr-2" /> Add Alarm Rule
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-4">
        {loading && (
          <div className="text-center py-12 text-gray-500 dark:text-gray-400">Loading alarm rules…</div>
        )}

        {!loading && loadError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-center">
            <p className="text-red-300 mb-3">{loadError}</p>
            <button type="button" onClick={() => void fetchRules()} className="btn-secondary px-4 py-2 text-sm w-auto">
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && rules.length === 0 && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-10 text-center">
            <p className="text-gray-600 dark:text-gray-300 mb-2">No alarm rules configured yet.</p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Create a Signal Loss rule to generate events when stream health confirms a camera is offline.
            </p>
            <button type="button" onClick={() => setIsCreateOpen(true)} className="btn-primary px-4 py-2 text-sm w-auto">
              Create Alarm Rule
            </button>
          </div>
        )}

        {!loading && !loadError && rules.length > 0 && (
          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
                <tr>
                  <th className="px-4 py-3">Rule Name</th>
                  <th className="px-4 py-3">Camera</th>
                  <th className="px-4 py-3">Trigger</th>
                  <th className="px-4 py-3">Severity</th>
                  <th className="px-4 py-3">Actions</th>
                  <th className="px-4 py-3">Cooldown</th>
                  <th className="px-4 py-3">Enabled</th>
                  <th className="px-4 py-3">Last Triggered</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr
                    key={rule.id}
                    className="border-b border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/20"
                  >
                    <td className="px-4 py-3 font-medium text-gray-900 dark:text-white">{rule.name}</td>
                    <td className="px-4 py-3">{resolveCameraName(rule.camera_id)}</td>
                    <td className="px-4 py-3">{triggerLabel(rule.trigger?.source_type || '')}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 text-xs font-semibold rounded-full ${severityBadgeClass(rule.severity)}`}
                      >
                        {severityLabel(rule.severity)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {rule.actions.map(actionLabel).join(', ') || '—'}
                    </td>
                    <td className="px-4 py-3">{rule.cooldown_seconds}s</td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => void handleToggleEnabled(rule)}
                        className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                          rule.enabled
                            ? 'bg-green-500/20 text-green-300'
                            : 'bg-gray-500/20 text-gray-400'
                        }`}
                        title={rule.enabled ? 'Click to disable' : 'Click to enable'}
                      >
                        {rule.enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">{formatLastTriggered(rule)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => setEditingRule(rule)}
                          className="p-2 text-gray-500 hover:text-gray-900 dark:hover:text-white rounded-md"
                          title="Edit rule"
                        >
                          <Edit size={16} />
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(rule)}
                          className="p-2 text-gray-500 hover:text-red-500 rounded-md"
                          title="Delete rule"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <AlarmRuleModal
        open={isCreateOpen || Boolean(editingRule)}
        rule={editingRule}
        cameras={cameraOptions}
        camerasLoading={camerasLoading}
        saving={saving}
        onClose={() => {
          setIsCreateOpen(false);
          setEditingRule(null);
        }}
        onSave={editingRule ? handleEdit : handleCreate}
      />

      <ConfirmModal
        open={Boolean(deleteTarget)}
        title="Delete alarm rule"
        body={
          deleteTarget ? (
            <p>
              Delete alarm rule &lsquo;{deleteTarget.name}&rsquo;? This cannot be undone.
            </p>
          ) : null
        }
        confirmLabel="Delete"
        danger
        busy={deleting}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void handleConfirmDelete()}
      />
    </div>
  );
}
