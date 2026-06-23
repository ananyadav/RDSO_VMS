import React, { useMemo, useEffect } from 'react';
import { Building2, Layers, MapPin } from 'lucide-react';
import {
  parseBuildingScopeKey,
  siteScopeKey,
  parseSiteScopeKey,
  type BuildingGroup,
} from '../constants/corporateFloors';
import {
  NO_FLOOR_SELECTED,
  buildingKey,
  isAdminUser,
  parseBuildingKey,
  soleFloorGroup,
} from '../lib/cameraAccess';

export type { BuildingGroup };

interface LiveViewLocationSelectorProps {
  buildings: BuildingGroup[];
  selectedSite: string | null;
  selectedBuildingKey: string | null;
  selectedGroup: string | null;
  onSelectSite: (site: string | null) => void;
  onSelectBuilding: (buildingKey: string | null) => void;
  onSelectGroup: (cameraGroup: string | null) => void;
}

const selectClass =
  'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-3 py-1.5 text-sm min-w-[11rem]';

function confirmBulkLoad(label: string, count: number): boolean {
  return window.confirm(
    `${label}\n\nThis will load ${count} camera stream${count === 1 ? '' : 's'} at once and may affect performance.\n\nContinue?`,
  );
}

export default function LiveViewLocationSelector({
  buildings,
  selectedSite,
  selectedBuildingKey,
  selectedGroup,
  onSelectSite,
  onSelectBuilding,
  onSelectGroup,
}: LiveViewLocationSelectorProps) {
  const isAdmin = isAdminUser();

  const sites = useMemo(() => {
    const map = new Map<string, BuildingGroup[]>();
    for (const b of buildings) {
      const site = b.site || 'Unknown';
      if (!map.has(site)) map.set(site, []);
      map.get(site)!.push(b);
    }
    return Array.from(map.entries()).map(([site, siteBuildings]) => ({
      site,
      buildings: siteBuildings,
      cameraCount: siteBuildings.reduce(
        (n, b) => n + b.floorGroups.reduce((s, fg) => s + (fg.cameraCount ?? 0), 0),
        0,
      ),
    }));
  }, [buildings]);

  const selectedSiteData = sites.find((s) => s.site === selectedSite);
  const isSiteAllCameras = Boolean(
    selectedSite && selectedGroup && parseSiteScopeKey(selectedGroup) === selectedSite,
  );

  const parsedBuilding = selectedBuildingKey ? parseBuildingKey(selectedBuildingKey) : null;
  const buildingDef =
    parsedBuilding &&
    buildings.find(
      (b) => b.site === parsedBuilding.site && b.building === parsedBuilding.building,
    );

  const floorGroups = buildingDef?.floorGroups ?? [];
  const onlyFloorGroup = soleFloorGroup(floorGroups);

  useEffect(() => {
    if (!selectedBuildingKey || isSiteAllCameras || !onlyFloorGroup) return;
    if (selectedGroup !== onlyFloorGroup) {
      onSelectGroup(onlyFloorGroup);
    }
  }, [selectedBuildingKey, isSiteAllCameras, onlyFloorGroup, selectedGroup, onSelectGroup]);

  const selectedFloor =
    selectedGroup &&
    !parseSiteScopeKey(selectedGroup) &&
    !selectedGroup.startsWith('__building__:')
      ? floorGroups.find((fg) => fg.camera_group === selectedGroup)
      : undefined;

  const locationHint = isSiteAllCameras
    ? `${selectedSite} (all cameras)`
    : selectedGroup?.startsWith('__building__:')
      ? (() => {
          const scope = parseBuildingScopeKey(selectedGroup);
          return scope ? `${scope.site} / ${scope.building} (all floors)` : '';
        })()
      : selectedFloor?.location_path ||
        (parsedBuilding ? `${parsedBuilding.site} / ${parsedBuilding.building}` : '');

  const handleSiteChange = (value: string) => {
    if (!value) {
      onSelectSite(null);
      onSelectBuilding(null);
      onSelectGroup(null);
      return;
    }
    onSelectSite(value);
    onSelectBuilding(null);
    onSelectGroup(null);
  };

  const handleBuildingChange = (value: string) => {
    if (!value) {
      onSelectBuilding(null);
      onSelectGroup(null);
      return;
    }
    if (selectedSite && value === siteScopeKey(selectedSite)) {
      if (!isAdmin) return;
      const count = selectedSiteData?.cameraCount ?? 0;
      if (!confirmBulkLoad(`Load all cameras in ${selectedSite}?`, count)) return;
      onSelectBuilding(null);
      onSelectGroup(siteScopeKey(selectedSite));
      return;
    }
    onSelectBuilding(value);
    const picked = parseBuildingKey(value);
    const def =
      picked &&
      buildings.find((b) => b.site === picked.site && b.building === picked.building);
    onSelectGroup(def ? soleFloorGroup(def.floorGroups) : null);
  };

  const handleFloorChange = (value: string) => {
    if (!value || value === NO_FLOOR_SELECTED) {
      onSelectGroup(null);
      return;
    }
    onSelectGroup(value);
  };

  const buildingSelectValue = isSiteAllCameras
    ? siteScopeKey(selectedSite!)
    : (selectedBuildingKey ?? '');

  const floorValue =
    selectedGroup && !parseSiteScopeKey(selectedGroup) ? selectedGroup : NO_FLOOR_SELECTED;

  return (
    <div className="flex flex-col lg:flex-row gap-3 items-start lg:items-center">
      <div className="flex items-center gap-2 min-w-0">
        <MapPin size={18} className="text-violet-400 shrink-0" />
        <label className="sr-only">Site / Unit</label>
        <select
          value={selectedSite ?? ''}
          onChange={(e) => handleSiteChange(e.target.value)}
          className={selectClass}
        >
          <option value="">Select site / unit…</option>
          {sites.map((s) => (
            <option key={s.site} value={s.site}>
              {s.site} — {s.cameraCount} camera{s.cameraCount === 1 ? '' : 's'}
            </option>
          ))}
        </select>
      </div>

      {selectedSite && (
        <div className="flex items-center gap-2 min-w-0">
          <Building2 size={18} className="text-emerald-400 shrink-0" />
          <label className="sr-only">Building / Area</label>
          <select
            value={buildingSelectValue}
            onChange={(e) => handleBuildingChange(e.target.value)}
            className={selectClass}
          >
            <option value="">Select building / area…</option>
            {isAdmin && selectedSiteData && (
              <option value={siteScopeKey(selectedSite)}>
                All cameras — {selectedSiteData.cameraCount} camera
                {selectedSiteData.cameraCount === 1 ? '' : 's'}
              </option>
            )}
            {selectedSiteData?.buildings.map((b) => {
              const count = b.floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);
              const key = buildingKey(b.site, b.building);
              return (
                <option key={key} value={key}>
                  {b.building} — {count} camera{count === 1 ? '' : 's'}
                </option>
              );
            })}
          </select>
        </div>
      )}

      {selectedBuildingKey && !isSiteAllCameras && floorGroups.length > 0 && (
        <div className="flex items-center gap-2 min-w-0">
          <Layers size={18} className="text-sky-400 shrink-0" />
          <label className="sr-only">Floor / Zone</label>
          {onlyFloorGroup && floorGroups[0] ? (
            <span className={`${selectClass} inline-flex items-center bg-gray-100 dark:bg-gray-800/80 text-gray-700 dark:text-gray-300 cursor-default`}>
              {floorGroups[0].floor_group || floorGroups[0].floor}
              {' — '}
              {floorGroups[0].cameraCount ?? 0} camera
              {(floorGroups[0].cameraCount ?? 0) === 1 ? '' : 's'}
            </span>
          ) : (
            <select
              value={floorValue}
              onChange={(e) => handleFloorChange(e.target.value)}
              className={selectClass}
            >
              <option value={NO_FLOOR_SELECTED}>Select floor / zone…</option>
              {floorGroups.map((fg) => (
                <option key={fg.camera_group} value={fg.camera_group}>
                  {fg.floor_group || fg.floor} — {fg.cameraCount} camera
                  {fg.cameraCount === 1 ? '' : 's'}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {selectedBuildingKey && !isSiteAllCameras && floorGroups.length === 0 && (
        <span className="text-sm text-gray-500">No floors configured for this area</span>
      )}

      {locationHint && selectedGroup && (
        <span className="text-xs text-gray-500 hidden xl:inline truncate max-w-md">{locationHint}</span>
      )}
    </div>
  );
}
