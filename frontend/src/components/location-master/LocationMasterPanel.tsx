import React, { useMemo, useState } from 'react';
import { Building2, Plus, Pencil, Trash2, Power } from 'lucide-react';
import toast from 'react-hot-toast';
import { apiFetch } from '../../lib/api';
import type { LocationSite } from '../../constants/corporateFloors';

interface LocationMasterPanelProps {
  sites: LocationSite[];
  onUpdated: () => void;
}

const inputClass =
  'w-full bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm';

function CameraCount({ count }: { count?: number }) {
  if (!count) return null;
  return (
    <span className="text-xs text-gray-500 font-normal ml-1.5">
      ({count} camera{count !== 1 ? 's' : ''})
    </span>
  );
}

export default function LocationMasterPanel({ sites, onUpdated }: LocationMasterPanelProps) {
  const [newSite, setNewSite] = useState('');
  const [addBuildingSiteId, setAddBuildingSiteId] = useState('');
  const [newBuilding, setNewBuilding] = useState('');
  const [floorsText, setFloorsText] = useState('Ground Floor\n1st Floor');
  const [addFloorSiteId, setAddFloorSiteId] = useState('');
  const [addFloorBuildingId, setAddFloorBuildingId] = useState('');
  const [newFloor, setNewFloor] = useState('');
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<
    | { kind: 'site'; id: string }
    | { kind: 'building'; siteId: string; id: string }
    | { kind: 'floor'; siteId: string; buildingId: string; name: string }
    | null
  >(null);

  const activeSites = useMemo(
    () => sites.filter((s) => s.is_active !== false),
    [sites],
  );

  const refresh = () => onUpdated();

  const handleError = async (res: Response, fallback: string) => {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || fallback);
  };

  const handleAddSite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSite.trim()) {
      toast.error('Site name is required');
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch('/api/locations/sites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newSite.trim() }),
      });
      if (!res.ok) await handleError(res, 'Failed to add site');
      toast.success(`Added site ${newSite.trim()}`);
      setNewSite('');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add site');
    } finally {
      setSaving(false);
    }
  };

  const handleAddBuilding = async (e: React.FormEvent) => {
    e.preventDefault();
    const floors = floorsText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    if (!addBuildingSiteId || !newBuilding.trim()) {
      toast.error('Select a site and enter a building name');
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch('/api/locations/buildings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          site_id: addBuildingSiteId,
          building: newBuilding.trim(),
          floors,
        }),
      });
      if (!res.ok) await handleError(res, 'Failed to add building');
      toast.success(`Added ${newBuilding.trim()}`);
      setNewBuilding('');
      setFloorsText('');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add building');
    } finally {
      setSaving(false);
    }
  };

  const handleAddFloor = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!addFloorSiteId || !addFloorBuildingId || !newFloor.trim()) {
      toast.error('Select site, building, and floor name');
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch('/api/locations/floors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          site_id: addFloorSiteId,
          building_id: addFloorBuildingId,
          floor: newFloor.trim(),
        }),
      });
      if (!res.ok) await handleError(res, 'Failed to add floor');
      toast.success(`Added floor ${newFloor.trim()}`);
      setNewFloor('');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to add floor');
    } finally {
      setSaving(false);
    }
  };

  const toggleSiteActive = async (site: LocationSite) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${site.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: site.is_active === false }),
      });
      if (!res.ok) await handleError(res, 'Failed to update site');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update site');
    } finally {
      setSaving(false);
    }
  };

  const deleteSite = async (site: LocationSite) => {
    if ((site.camera_count ?? 0) > 0) {
      toast.error(`Cannot delete: ${site.camera_count} camera(s) assigned. Disable the site instead.`);
      return;
    }
    if (!window.confirm(`Delete site "${site.name}" and all its buildings/floors?`)) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${site.id}`, { method: 'DELETE' });
      if (!res.ok) await handleError(res, 'Failed to delete site');
      toast.success(`Deleted ${site.name}`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete site');
    } finally {
      setSaving(false);
    }
  };

  const renameSite = async (site: LocationSite, name: string) => {
    if (!name.trim() || name.trim() === site.name) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${site.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) await handleError(res, 'Failed to rename site');
      setEditing(null);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to rename site');
    } finally {
      setSaving(false);
    }
  };

  const renameBuilding = async (
    siteId: string,
    buildingId: string,
    currentName: string,
    name: string,
  ) => {
    if (!name.trim() || name.trim() === currentName) {
      setEditing(null);
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${siteId}/buildings/${buildingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), site_id: siteId }),
      });
      if (!res.ok) await handleError(res, 'Failed to rename building');
      toast.success(`Renamed to ${name.trim()}`);
      setEditing(null);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to rename building');
    } finally {
      setSaving(false);
    }
  };

  const patchBuilding = async (
    siteId: string,
    buildingId: string,
    body: Record<string, unknown>,
  ) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${siteId}/buildings/${buildingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ site_id: siteId, ...body }),
      });
      if (!res.ok) await handleError(res, 'Failed to update building');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update building');
    } finally {
      setSaving(false);
    }
  };

  const deleteBuilding = async (
    siteId: string,
    buildingId: string,
    name: string,
    cameraCount?: number,
  ) => {
    if ((cameraCount ?? 0) > 0) {
      toast.error(`Cannot delete: ${cameraCount} camera(s) assigned. Disable the building instead.`);
      return;
    }
    if (!window.confirm(`Delete building "${name}"?`)) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/sites/${siteId}/buildings/${buildingId}`, {
        method: 'DELETE',
      });
      if (!res.ok) await handleError(res, 'Failed to delete building');
      toast.success(`Deleted ${name}`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete building');
    } finally {
      setSaving(false);
    }
  };

  const renameFloor = async (
    siteId: string,
    buildingId: string,
    currentName: string,
    name: string,
  ) => {
    if (!name.trim() || name.trim() === currentName) {
      setEditing(null);
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/floors/${encodeURIComponent(currentName)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          site_id: siteId,
          building_id: buildingId,
          name: name.trim(),
        }),
      });
      if (!res.ok) await handleError(res, 'Failed to rename floor');
      toast.success(`Renamed to ${name.trim()}`);
      setEditing(null);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to rename floor');
    } finally {
      setSaving(false);
    }
  };

  const patchFloor = async (
    siteId: string,
    buildingId: string,
    floorName: string,
    body: Record<string, unknown>,
  ) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/locations/floors/${encodeURIComponent(floorName)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ site_id: siteId, building_id: buildingId, ...body }),
      });
      if (!res.ok) await handleError(res, 'Failed to update floor');
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update floor');
    } finally {
      setSaving(false);
    }
  };

  const deleteFloor = async (
    siteId: string,
    buildingId: string,
    floorName: string,
    cameraCount?: number,
  ) => {
    if ((cameraCount ?? 0) > 0) {
      toast.error(`Cannot delete: ${cameraCount} camera(s) assigned. Disable the floor instead.`);
      return;
    }
    if (!window.confirm(`Delete floor "${floorName}"?`)) return;
    setSaving(true);
    try {
      const q = new URLSearchParams({ site_id: siteId, building_id: buildingId });
      const res = await apiFetch(
        `/api/locations/floors/${encodeURIComponent(floorName)}?${q}`,
        { method: 'DELETE' },
      );
      if (!res.ok) await handleError(res, 'Failed to delete floor');
      toast.success(`Deleted ${floorName}`);
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete floor');
    } finally {
      setSaving(false);
    }
  };

  const floorBuildings = sites.find((s) => s.id === addFloorSiteId)?.buildings ?? [];

  return (
    <div className="space-y-6">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Manage Site/Unit → Building/Department/Area → Floor/Zone/Sub-area. Locations are stored in
            the database and appear in camera forms immediately after save. Disable instead of delete when
            cameras are assigned.
          </p>
          <section>
            <h4 className="text-sm font-semibold text-gray-500 uppercase mb-3">Location hierarchy</h4>
            {sites.length === 0 ? (
              <p className="text-sm text-gray-500">No sites configured.</p>
            ) : (
              <ul className="space-y-4">
                {sites.map((site) => (
                  <li
                    key={site.id}
                    className={`rounded-lg border p-3 ${
                      site.is_active === false
                        ? 'border-gray-300 dark:border-gray-600 opacity-60'
                        : 'border-gray-200 dark:border-gray-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <div className="min-w-0 flex-1">
                        {editing?.kind === 'site' && editing.id === site.id ? (
                          <input
                            className={inputClass}
                            defaultValue={site.name}
                            autoFocus
                            onBlur={(e) => void renameSite(site, e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') void renameSite(site, e.currentTarget.value);
                              if (e.key === 'Escape') setEditing(null);
                            }}
                          />
                        ) : (
                          <div className="font-semibold text-gray-900 dark:text-gray-100">
                            {site.name}
                            <CameraCount count={site.camera_count} />
                          </div>
                        )}
                        {site.is_active === false && (
                          <span className="text-xs text-amber-500">Disabled</span>
                        )}
                      </div>
                      <div className="flex gap-1 shrink-0">
                        <button
                          type="button"
                          title="Rename"
                          className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500"
                          onClick={() => setEditing({ kind: 'site', id: site.id })}
                        >
                          <Pencil size={14} />
                        </button>
                        <button
                          type="button"
                          title={site.is_active === false ? 'Enable' : 'Disable'}
                          className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500"
                          onClick={() => void toggleSiteActive(site)}
                          disabled={saving}
                        >
                          <Power size={14} />
                        </button>
                        <button
                          type="button"
                          title={
                            (site.camera_count ?? 0) > 0
                              ? 'Cannot delete — cameras assigned. Disable instead.'
                              : 'Delete'
                          }
                          className="p-1.5 rounded hover:bg-red-900/30 text-red-400 disabled:opacity-30"
                          onClick={() => void deleteSite(site)}
                          disabled={saving || (site.camera_count ?? 0) > 0}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    <ul className="ml-3 space-y-2 border-l border-gray-200 dark:border-gray-700 pl-3">
                      {(site.buildings || []).map((b) => (
                        <li key={b.id}>
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-1.5 min-w-0 flex-1">
                              <Building2 size={14} className="text-emerald-400 shrink-0" />
                              {editing?.kind === 'building' &&
                              editing.siteId === site.id &&
                              editing.id === b.id ? (
                                <input
                                  className={`${inputClass} py-1`}
                                  defaultValue={b.name}
                                  autoFocus
                                  disabled={saving}
                                  onBlur={(e) =>
                                    void renameBuilding(site.id, b.id, b.name, e.target.value)
                                  }
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                      void renameBuilding(
                                        site.id,
                                        b.id,
                                        b.name,
                                        e.currentTarget.value,
                                      );
                                    }
                                    if (e.key === 'Escape') setEditing(null);
                                  }}
                                />
                              ) : (
                                <span
                                  className={`text-sm font-medium truncate ${
                                    b.is_active === false ? 'line-through text-gray-500' : ''
                                  }`}
                                >
                                  {b.name}
                                  <CameraCount count={b.camera_count} />
                                </span>
                              )}
                            </div>
                            <div className="flex gap-1 shrink-0">
                              <button
                                type="button"
                                title="Rename"
                                className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500"
                                onClick={() =>
                                  setEditing({ kind: 'building', siteId: site.id, id: b.id })
                                }
                                disabled={saving}
                              >
                                <Pencil size={12} />
                              </button>
                              <button
                                type="button"
                                title={b.is_active === false ? 'Enable' : 'Disable'}
                                className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500"
                                onClick={() =>
                                  void patchBuilding(site.id, b.id, {
                                    is_active: b.is_active === false,
                                  })
                                }
                                disabled={saving}
                              >
                                <Power size={12} />
                              </button>
                              <button
                                type="button"
                                title={
                                  (b.camera_count ?? 0) > 0
                                    ? 'Cannot delete — cameras assigned. Disable instead.'
                                    : 'Delete'
                                }
                                className="p-1 rounded hover:bg-red-900/30 text-red-400 disabled:opacity-30"
                                onClick={() => void deleteBuilding(site.id, b.id, b.name, b.camera_count)}
                                disabled={saving || (b.camera_count ?? 0) > 0}
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-1 mt-1 ml-5">
                            {(b.floors || []).map((f) => (
                              <span
                                key={`${b.id}-${f.name}`}
                                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${
                                  f.is_active === false
                                    ? 'bg-gray-100 dark:bg-gray-800 text-gray-500 line-through'
                                    : 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300'
                                }`}
                              >
                                {editing?.kind === 'floor' &&
                                editing.siteId === site.id &&
                                editing.buildingId === b.id &&
                                editing.name === f.name ? (
                                  <input
                                    className="bg-transparent border-b border-gray-400 dark:border-gray-500 outline-none text-xs w-24 min-w-0"
                                    defaultValue={f.name}
                                    autoFocus
                                    disabled={saving}
                                    onBlur={(e) =>
                                      void renameFloor(site.id, b.id, f.name, e.target.value)
                                    }
                                    onKeyDown={(e) => {
                                      if (e.key === 'Enter') {
                                        void renameFloor(
                                          site.id,
                                          b.id,
                                          f.name,
                                          e.currentTarget.value,
                                        );
                                      }
                                      if (e.key === 'Escape') setEditing(null);
                                    }}
                                  />
                                ) : (
                                  <>
                                    {f.name}
                                    <CameraCount count={f.camera_count} />
                                  </>
                                )}
                                <button
                                  type="button"
                                  className="hover:text-sky-400"
                                  title="Rename"
                                  onClick={() =>
                                    setEditing({
                                      kind: 'floor',
                                      siteId: site.id,
                                      buildingId: b.id,
                                      name: f.name,
                                    })
                                  }
                                  disabled={saving}
                                >
                                  <Pencil size={10} />
                                </button>
                                <button
                                  type="button"
                                  className="hover:text-amber-400"
                                  title={f.is_active === false ? 'Enable' : 'Disable'}
                                  onClick={() =>
                                    void patchFloor(site.id, b.id, f.name, {
                                      is_active: f.is_active === false,
                                    })
                                  }
                                  disabled={saving}
                                >
                                  <Power size={10} />
                                </button>
                                <button
                                  type="button"
                                  className="hover:text-red-400 disabled:opacity-30"
                                  title={
                                    (f.camera_count ?? 0) > 0
                                      ? 'Cannot delete — cameras assigned. Disable instead.'
                                      : 'Delete'
                                  }
                                  onClick={() =>
                                    void deleteFloor(site.id, b.id, f.name, f.camera_count)
                                  }
                                  disabled={saving || (f.camera_count ?? 0) > 0}
                                >
                                  <Trash2 size={10} />
                                </button>
                              </span>
                            ))}
                            {(b.floors || []).length === 0 && (
                              <span className="text-xs text-gray-500 italic">No floors</span>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <form onSubmit={handleAddSite} className="space-y-3 border-t border-gray-200 dark:border-gray-700 pt-5">
            <h4 className="text-sm font-semibold text-emerald-300 uppercase flex items-center gap-2">
              <Plus size={16} /> Add site / unit
            </h4>
            <div className="flex gap-2">
              <input
                className={inputClass}
                value={newSite}
                onChange={(e) => setNewSite(e.target.value)}
                placeholder="e.g. RML - 7"
              />
              <button
                type="submit"
                disabled={saving}
                className="px-4 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm shrink-0 disabled:opacity-50"
              >
                Add site
              </button>
            </div>
          </form>

          <form onSubmit={handleAddBuilding} className="space-y-3 border-t border-gray-200 dark:border-gray-700 pt-5">
            <h4 className="text-sm font-semibold text-sky-300 uppercase flex items-center gap-2">
              <Plus size={16} /> Add building / department / area
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Site *</label>
                <select
                  className={inputClass}
                  value={addBuildingSiteId}
                  onChange={(e) => setAddBuildingSiteId(e.target.value)}
                  required
                >
                  <option value="">Select site…</option>
                  {activeSites.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Building *</label>
                <input
                  className={inputClass}
                  value={newBuilding}
                  onChange={(e) => setNewBuilding(e.target.value)}
                  placeholder="e.g. Corporate Office"
                  required
                />
              </div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Floors / zones (one per line, optional)</label>
              <textarea
                className={`${inputClass} min-h-[80px]`}
                value={floorsText}
                onChange={(e) => setFloorsText(e.target.value)}
                placeholder={'Ground Floor\n1st Floor'}
              />
            </div>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 rounded bg-sky-600 hover:bg-sky-500 text-white text-sm disabled:opacity-50"
            >
              Add building
            </button>
          </form>

          <form onSubmit={handleAddFloor} className="space-y-3 border-t border-gray-200 dark:border-gray-700 pt-5">
            <h4 className="text-sm font-semibold text-violet-300 uppercase flex items-center gap-2">
              <Plus size={16} /> Add floor / zone / sub-area
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Site</label>
                <select
                  className={inputClass}
                  value={addFloorSiteId}
                  onChange={(e) => {
                    setAddFloorSiteId(e.target.value);
                    setAddFloorBuildingId('');
                  }}
                >
                  <option value="">Select…</option>
                  {activeSites.map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Building</label>
                <select
                  className={inputClass}
                  value={addFloorBuildingId}
                  onChange={(e) => setAddFloorBuildingId(e.target.value)}
                >
                  <option value="">Select…</option>
                  {floorBuildings
                    .filter((b) => b.is_active !== false)
                    .map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Floor name</label>
                <input
                  className={inputClass}
                  value={newFloor}
                  onChange={(e) => setNewFloor(e.target.value)}
                  placeholder="e.g. Ground Floor"
                />
              </div>
            </div>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 rounded bg-violet-600 hover:bg-violet-500 text-white text-sm disabled:opacity-50"
            >
              Add floor
            </button>
          </form>
    </div>
  );
}
