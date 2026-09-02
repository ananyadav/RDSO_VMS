import React, { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Edit, Plus, Trash2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import CameraSequenceModal, { type SequenceCameraOption } from '../components/CameraSequenceModal';
import ConfirmModal from '../components/control-center/ConfirmModal';
import { formValuesToPayload } from '../lib/cameraSequenceForm';
import {
  CameraSequencesRequestError,
  createCameraSequence,
  deleteCameraSequence,
  listCameraSequences,
  updateCameraSequence,
  type CameraSequence,
} from '../lib/cameraSequencesApi';
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
}

function cameraLabel(cam: ConfiguredCamera): string {
  return (
    (cam.display_name || cam.name || '').trim() ||
    cam.ip_address ||
    cam._id
  );
}

function formatCameraSummary(
  sequence: CameraSequence,
  resolveName: (id: string) => string,
): string {
  if (!sequence.camera_ids.length) return '—';
  return sequence.camera_ids.map((id, i) => `${i + 1}. ${resolveName(id)}`).join(' → ');
}

export default function CameraSequences(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [sequences, setSequences] = useState<CameraSequence[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cameras, setCameras] = useState<ConfiguredCamera[]>([]);
  const [camerasLoading, setCamerasLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(() =>
    paramFlag(initialParams.current?.get('add') ?? null, false),
  );
  const [editingSequence, setEditingSequence] = useState<CameraSequence | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<CameraSequence | null>(null);

  const cameraById = useMemo(() => {
    const map = new Map<string, ConfiguredCamera>();
    for (const cam of cameras) map.set(cam._id, cam);
    return map;
  }, [cameras]);

  const cameraOptions: SequenceCameraOption[] = useMemo(
    () =>
      cameras.map((cam) => ({
        id: cam._id,
        label: cameraLabel(cam),
        ip_address: cam.ip_address,
      })),
    [cameras],
  );

  const fetchSequences = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listCameraSequences({ limit: 200 });
      setSequences(data.items);
    } catch (err) {
      const message =
        err instanceof CameraSequencesRequestError
          ? err.message
          : err instanceof Error
            ? err.message
            : 'Failed to load camera sequences';
      setLoadError(message);
      setSequences([]);
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
    void fetchSequences();
    void fetchCameras();
  }, [fetchSequences, fetchCameras]);

  useEffect(() => {
    const editId = initialStringParam(initialParams, 'edit');
    if (editId && sequences.length > 0 && !editingSequence) {
      const match = sequences.find((s) => s.id === editId);
      if (match) setEditingSequence(match);
    }
    markHydrated();
  }, [sequences, editingSequence, initialParams, markHydrated]);

  const urlValues = useMemo(
    () => ({
      add: isCreateOpen ? '1' : null,
      edit: editingSequence?.id ?? null,
    }),
    [isCreateOpen, editingSequence],
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
      const created = await createCameraSequence(payload);
      toast.success('Camera sequence created');
      setSequences((prev) => [created, ...prev.filter((s) => s.id !== created.id)]);
      setIsCreateOpen(false);
    } catch (err) {
      toast.error(
        err instanceof CameraSequencesRequestError ? err.message : 'Failed to create sequence',
      );
      throw err;
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = async (payload: ReturnType<typeof formValuesToPayload>) => {
    if (!editingSequence) return;
    setSaving(true);
    try {
      const updated = await updateCameraSequence(editingSequence.id, payload);
      toast.success('Camera sequence updated');
      setSequences((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setEditingSequence(null);
    } catch (err) {
      toast.error(
        err instanceof CameraSequencesRequestError ? err.message : 'Failed to update sequence',
      );
      throw err;
    } finally {
      setSaving(false);
    }
  };

  const handleToggleEnabled = async (sequence: CameraSequence) => {
    const next = !sequence.enabled;
    try {
      const updated = await updateCameraSequence(sequence.id, { enabled: next });
      setSequences((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      toast.success(next ? 'Sequence enabled' : 'Sequence disabled');
    } catch (err) {
      toast.error(
        err instanceof CameraSequencesRequestError ? err.message : 'Failed to update sequence',
      );
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteCameraSequence(deleteTarget.id);
      setSequences((prev) => prev.filter((s) => s.id !== deleteTarget.id));
      toast.success('Camera sequence deleted');
      setDeleteTarget(null);
    } catch (err) {
      toast.error(
        err instanceof CameraSequencesRequestError ? err.message : 'Failed to delete sequence',
      );
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Camera Sequences"
        subtitle="Define ordered camera patrol routes for Live View rotation"
        rightContent={
          <button
            type="button"
            onClick={() => setIsCreateOpen(true)}
            className="btn-primary flex items-center w-auto"
          >
            <Plus size={18} className="mr-2" /> Add Camera Sequence
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-4">
        {loading && (
          <div className="text-center py-12 text-gray-500 dark:text-gray-400">Loading camera sequences…</div>
        )}

        {!loading && loadError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-center">
            <p className="text-red-300 mb-3">{loadError}</p>
            <button
              type="button"
              onClick={() => void fetchSequences()}
              className="btn-secondary px-4 py-2 text-sm w-auto"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && sequences.length === 0 && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-10 text-center">
            <p className="text-gray-600 dark:text-gray-300 mb-2">No camera sequences configured yet.</p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              Create an ordered list of cameras with dwell time for Live View patrol playback.
            </p>
            <button
              type="button"
              onClick={() => setIsCreateOpen(true)}
              className="btn-primary px-4 py-2 text-sm w-auto"
            >
              Add Camera Sequence
            </button>
          </div>
        )}

        {!loading && !loadError && sequences.length > 0 && (
          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
                <tr>
                  <th className="px-4 py-3">Sequence Name</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">Cameras</th>
                  <th className="px-4 py-3">Order</th>
                  <th className="px-4 py-3">Dwell</th>
                  <th className="px-4 py-3">Enabled</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {sequences.map((sequence) => (
                  <tr
                    key={sequence.id}
                    className="border-b border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/20"
                  >
                    <td className="px-4 py-3 font-medium text-gray-900 dark:text-white">{sequence.name}</td>
                    <td className="px-4 py-3 max-w-xs truncate">{sequence.description || '—'}</td>
                    <td className="px-4 py-3">{sequence.camera_ids.length}</td>
                    <td className="px-4 py-3 max-w-md truncate" title={formatCameraSummary(sequence, resolveCameraName)}>
                      {formatCameraSummary(sequence, resolveCameraName)}
                    </td>
                    <td className="px-4 py-3">{sequence.dwell_seconds}s</td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => void handleToggleEnabled(sequence)}
                        className={`px-2 py-0.5 text-xs font-semibold rounded-full ${
                          sequence.enabled
                            ? 'bg-green-500/20 text-green-300'
                            : 'bg-gray-500/20 text-gray-400'
                        }`}
                        title={sequence.enabled ? 'Click to disable' : 'Click to enable'}
                      >
                        {sequence.enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => setEditingSequence(sequence)}
                          className="p-2 text-gray-500 hover:text-gray-900 dark:hover:text-white rounded-md"
                          title="Edit sequence"
                        >
                          <Edit size={16} />
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(sequence)}
                          className="p-2 text-gray-500 hover:text-red-500 rounded-md"
                          title="Delete sequence"
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

      <CameraSequenceModal
        open={isCreateOpen || Boolean(editingSequence)}
        sequence={editingSequence}
        cameras={cameraOptions}
        camerasLoading={camerasLoading}
        saving={saving}
        onClose={() => {
          setIsCreateOpen(false);
          setEditingSequence(null);
        }}
        onSubmit={editingSequence ? handleEdit : handleCreate}
      />

      <ConfirmModal
        open={Boolean(deleteTarget)}
        title="Delete Camera Sequence"
        body={
          deleteTarget ? (
            <p>
              Delete camera sequence &ldquo;{deleteTarget.name}&rdquo;? This cannot be undone.
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
