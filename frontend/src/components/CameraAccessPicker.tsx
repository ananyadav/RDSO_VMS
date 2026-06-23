import React, { useEffect, useMemo, useState } from 'react';
import { Building2, ChevronDown, ChevronRight, Layers, MapPin } from 'lucide-react';
import { apiFetch, cameraQuery } from '../lib/api';
import type { BuildingNode, FloorGroupNode } from './camera-management/LocationTreePanel';
import CameraAccessTile, { type AccessCamera } from './CameraAccessTile';

interface CameraAccessPickerProps {
  allowedCameraGroups: string[];
  allowedCameraUids: string[];
  onChange: (groups: string[], uids: string[]) => void;
}

function normalizeAccess(
  groups: string[] = [],
  uids: string[] = [],
): {
  allowedCameraGroups: string[];
  allowedCameraUids: string[];
  accessType?: 'all';
} {
  return {
    allowedCameraGroups: [...groups],
    allowedCameraUids: [...uids],
  };
}

/** Migrate legacy saved access shapes when editing an existing user. */
export function normalizeStoredCameraAccess(
  raw?: {
    allowedCameraGroups?: string[];
    allowedCameraUids?: string[];
    allowedGroups?: string[];
    allowedCameraIds?: string[];
    accessType?: string;
  } | null,
): {
  allowedCameraGroups: string[];
  allowedCameraUids: string[];
  accessType?: 'all';
} {
  if (!raw) return { allowedCameraGroups: [], allowedCameraUids: [] };
  const groups = raw.allowedCameraGroups?.length
    ? raw.allowedCameraGroups
    : raw.allowedGroups ?? [];
  const uids = raw.allowedCameraUids ?? [];
  if (raw.accessType === 'all' && !groups.length && !uids.length) {
    return { allowedCameraGroups: [], allowedCameraUids: [], accessType: 'all' };
  }
  return normalizeAccess(groups, uids);
}

export default function CameraAccessPicker({
  allowedCameraGroups,
  allowedCameraUids,
  onChange,
}: CameraAccessPickerProps) {
  const [buildings, setBuildings] = useState<BuildingNode[]>([]);
  const [loadingTree, setLoadingTree] = useState(true);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [selectedFloorLabel, setSelectedFloorLabel] = useState('');
  const [cameras, setCameras] = useState<AccessCamera[]>([]);
  const [loadingCameras, setLoadingCameras] = useState(false);
  const [streamSession, setStreamSession] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadingTree(true);
    void apiFetch('/api/cameras/groups?includeInactive=1&includeStats=0')
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        setBuildings(data.buildings ?? []);
      })
      .catch(() => {
        if (!cancelled) setBuildings([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingTree(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedGroup) {
      setCameras([]);
      return;
    }

    const controller = new AbortController();
    setLoadingCameras(true);
    setStreamSession((s) => s + 1);

    const q = cameraQuery({
      camera_group: selectedGroup,
      includeInactive: '1',
    });

    void apiFetch(`/api/cameras${q}`, { signal: controller.signal })
      .then((r) => r.json())
      .then((rows: AccessCamera[]) => {
        if (controller.signal.aborted) return;
        setCameras(Array.isArray(rows) ? rows : []);
      })
      .catch(() => {
        if (!controller.signal.aborted) setCameras([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingCameras(false);
      });

    return () => {
      controller.abort();
      setStreamSession((s) => s + 1);
    };
  }, [selectedGroup]);

  const floorGroupGranted = selectedGroup
    ? allowedCameraGroups.includes(selectedGroup)
    : false;

  const handleSelectFloor = (building: string, cameraGroup: string) => {
    setSelectedGroup(cameraGroup);
    const fg = buildings
      .flatMap((b) => b.floorGroups.map((f) => ({ b, f })))
      .find((x) => x.f.camera_group === cameraGroup);
    setSelectedFloorLabel(
      fg?.f.location_path || fg?.f.floor_group || fg?.f.floor || cameraGroup,
    );
  };

  const toggleGroup = (group: string) => {
    const next = allowedCameraGroups.includes(group)
      ? allowedCameraGroups.filter((g) => g !== group)
      : [...allowedCameraGroups, group];
    onChange(next, allowedCameraUids);
  };

  const toggleUid = (uid: string) => {
    const next = allowedCameraUids.includes(uid)
      ? allowedCameraUids.filter((u) => u !== uid)
      : [...allowedCameraUids, uid];
    onChange(allowedCameraGroups, next);
  };

  const grantCount = allowedCameraGroups.length + allowedCameraUids.length;

  return (
    <div className="flex flex-col gap-3 min-h-[420px]">
      <p className="text-xs text-gray-400">
        Select a floor on the left to preview its cameras (substream 102). Grant access per floor
        (camera group) or per individual camera.{' '}
        {grantCount > 0 && (
          <span className="text-emerald-400">
            {allowedCameraGroups.length} floor(s), {allowedCameraUids.length} individual camera(s).
          </span>
        )}
      </p>

      <div className="flex flex-1 min-h-0 gap-3 border border-gray-700 rounded-lg overflow-hidden">
        <div className="w-56 shrink-0 border-r border-gray-700 bg-gray-900/40 flex flex-col min-h-0">
          <div className="px-2 py-2 text-[10px] uppercase font-semibold text-gray-500 border-b border-gray-700">
            Locations
          </div>
          <div className="flex-1 overflow-y-auto min-h-0 max-h-[50vh]">
            <AccessLocationTree
              buildings={buildings}
              loading={loadingTree}
              selectedGroup={selectedGroup}
              allowedCameraGroups={allowedCameraGroups}
              onSelect={handleSelectFloor}
              onToggleGroup={toggleGroup}
            />
          </div>
        </div>

        <div className="flex-1 flex flex-col min-w-0 min-h-0 bg-gray-900/20">
          {!selectedGroup ? (
            <div className="flex-1 flex items-center justify-center text-sm text-gray-500 p-6 text-center min-h-[280px]">
              Select a floor or zone on the left to load live camera previews for that location.
            </div>
          ) : (
            <>
              <div className="shrink-0 flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-700 bg-gray-800/50">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-gray-200 truncate">
                    {selectedFloorLabel}
                  </div>
                  <div className="text-[10px] text-gray-500 font-mono truncate">{selectedGroup}</div>
                </div>
                <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer shrink-0">
                  <input
                    type="checkbox"
                    checked={floorGroupGranted}
                    onChange={() => toggleGroup(selectedGroup)}
                    className="checkbox-style"
                  />
                  Grant entire floor
                </label>
              </div>

              <div className="flex-1 overflow-y-auto p-3 min-h-0 max-h-[50vh]">
                {loadingCameras ? (
                  <p className="text-sm text-gray-500">Loading cameras…</p>
                ) : cameras.length === 0 ? (
                  <p className="text-sm text-gray-500">No cameras on this floor.</p>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {cameras.map((cam) => {
                      const uid = cam.cameraUid || cam.id;
                      return (
                        <CameraAccessTile
                          key={cam.id}
                          camera={cam}
                          checked={allowedCameraUids.includes(uid)}
                          groupGranted={allowedCameraGroups.includes(
                            cam.camera_group || selectedGroup,
                          )}
                          onToggle={toggleUid}
                          active={selectedGroup === (cam.camera_group || selectedGroup)}
                          streamSession={streamSession}
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

interface AccessLocationTreeProps {
  buildings: BuildingNode[];
  loading: boolean;
  selectedGroup: string | null;
  allowedCameraGroups: string[];
  onSelect: (building: string, cameraGroup: string) => void;
  onToggleGroup: (group: string) => void;
}

function AccessLocationTree({
  buildings,
  loading,
  selectedGroup,
  allowedCameraGroups,
  onSelect,
  onToggleGroup,
}: AccessLocationTreeProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const sites = useMemo(() => {
    const map = new Map<string, BuildingNode[]>();
    for (const b of buildings) {
      const site = b.site || 'Unknown';
      if (!map.has(site)) map.set(site, []);
      map.get(site)!.push(b);
    }
    return Array.from(map.entries()).map(([site, siteBuildings]) => ({
      site,
      buildings: siteBuildings,
    }));
  }, [buildings]);

  useEffect(() => {
    const init: Record<string, boolean> = {};
    for (const { site, buildings: bs } of sites) {
      init[site] = true;
      for (const b of bs) {
        init[`${site}::${b.building}`] = true;
      }
    }
    setExpanded((prev) => ({ ...init, ...prev }));
  }, [sites]);

  if (loading) {
    return <div className="p-3 text-xs text-gray-500">Loading locations…</div>;
  }

  if (buildings.length === 0) {
    return <div className="p-3 text-xs text-gray-500">No locations configured.</div>;
  }

  return (
    <div className="p-1.5 space-y-0.5 text-sm">
      {sites.map(({ site, buildings: siteBuildings }) => {
        const siteOpen = expanded[site] !== false;
        return (
          <div key={site}>
            <button
              type="button"
              className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-md text-left hover:bg-gray-800/80"
              onClick={() => setExpanded((e) => ({ ...e, [site]: !siteOpen }))}
            >
              {siteOpen ? (
                <ChevronDown size={14} className="shrink-0 text-gray-500" />
              ) : (
                <ChevronRight size={14} className="shrink-0 text-gray-500" />
              )}
              <MapPin size={14} className="shrink-0 text-violet-400" />
              <span className="flex-1 truncate font-semibold text-gray-100">{site}</span>
            </button>
            {siteOpen && (
              <div className="ml-2 border-l border-gray-700 pl-1">
                {siteBuildings.map((b) => {
                  const bKey = `${site}::${b.building}`;
                  const isOpen = expanded[bKey] !== false;
                  return (
                    <div key={bKey}>
                      <button
                        type="button"
                        className="w-full flex items-center gap-1.5 px-2 py-1 rounded-md text-left hover:bg-gray-800/80"
                        onClick={() => setExpanded((e) => ({ ...e, [bKey]: !isOpen }))}
                      >
                        {isOpen ? (
                          <ChevronDown size={13} className="shrink-0 text-gray-500" />
                        ) : (
                          <ChevronRight size={13} className="shrink-0 text-gray-500" />
                        )}
                        <Building2 size={13} className="shrink-0 text-emerald-400" />
                        <span className="flex-1 truncate font-medium text-gray-200">
                          {b.building}
                        </span>
                      </button>
                      {isOpen && (
                        <ul className="ml-4 mb-1 space-y-0.5">
                          {b.floorGroups.map((fg) => {
                            const selected = selectedGroup === fg.camera_group;
                            const granted = allowedCameraGroups.includes(fg.camera_group);
                            return (
                              <li key={fg.camera_group} className="flex items-center gap-0.5">
                                <input
                                  type="checkbox"
                                  title="Grant entire floor"
                                  checked={granted}
                                  onChange={() => onToggleGroup(fg.camera_group)}
                                  className="checkbox-style shrink-0 ml-1"
                                  onClick={(e) => e.stopPropagation()}
                                />
                                <button
                                  type="button"
                                  className={`flex-1 flex items-center gap-1.5 px-1.5 py-1 rounded-md text-left min-w-0 ${
                                    selected
                                      ? 'bg-sky-500/15 text-sky-100 ring-1 ring-sky-500/40'
                                      : 'hover:bg-gray-800/80 text-gray-300'
                                  }`}
                                  onClick={() => onSelect(b.building, fg.camera_group)}
                                >
                                  <Layers size={12} className="shrink-0 text-sky-400" />
                                  <span className="flex-1 truncate text-xs">
                                    {fg.floor_group || fg.floor}
                                  </span>
                                  <span className="text-[10px] text-gray-500 tabular-nums">
                                    {fg.cameraCount}
                                  </span>
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
