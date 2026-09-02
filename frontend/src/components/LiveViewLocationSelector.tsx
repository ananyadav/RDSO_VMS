import React, { useMemo, useEffect } from 'react';
import { Building2, Layers, MapPin } from 'lucide-react';
import {
  parseBuildingScopeKey,
  siteScopeKey,
  parseSiteScopeKey,
  buildingScopeKey,
  type BuildingGroup,
} from '../constants/corporateFloors';
import {
  NO_FLOOR_SELECTED,
  buildingKey,
  parseBuildingKey,
  soleFloorGroup,
} from '../lib/cameraAccess';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';

export type { BuildingGroup };

interface LiveViewLocationSelectorProps {
  buildings: BuildingGroup[];
  /** Site names from Location Master with no cameras yet (still shown in unit picker). */
  extraSiteNames?: string[];
  selectedSite: string | null;
  selectedBuildingKey: string | null;
  selectedGroup: string | null;
  onSelectSite: (site: string | null) => void;
  onSelectBuilding: (buildingKey: string | null) => void;
  onSelectGroup: (cameraGroup: string | null) => void;
}

const selectClass =
  'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-2.5 py-1 sm:px-3 sm:py-1.5 text-sm w-full sm:w-auto sm:min-w-[11rem]';

function confirmBulkLoad(label: string, count: number): boolean {
  return window.confirm(
    `${label}\n\nLoading ${count} camera${count === 1 ? '' : 's'} at once can slow down streaming and reduce overall performance. For faster loading, select a building or floor instead.\n\nContinue anyway?`,
  );
}

export default function LiveViewLocationSelector({
  buildings,
  extraSiteNames = [],
  selectedSite,
  selectedBuildingKey,
  selectedGroup,
  onSelectSite,
  onSelectBuilding,
  onSelectGroup,
}: LiveViewLocationSelectorProps) {
  const isAdmin = isOpsAdminUser(authService.getCurrentUser());

  const sites = useMemo(() => {
    const map = new Map<string, BuildingGroup[]>();
    for (const b of buildings) {
      const site = b.site || 'Unknown';
      if (!map.has(site)) map.set(site, []);
      map.get(site)!.push(b);
    }
    for (const site of extraSiteNames) {
      const name = (site || '').trim();
      if (name && !map.has(name)) map.set(name, []);
    }
    return Array.from(map.entries()).map(([site, siteBuildings]) => ({
      site,
      buildings: siteBuildings,
      cameraCount: siteBuildings.reduce(
        (n, b) => n + b.floorGroups.reduce((s, fg) => s + (fg.cameraCount ?? 0), 0),
        0,
      ),
    }));
  }, [buildings, extraSiteNames]);

  const selectedSiteData = sites.find((s) => s.site === selectedSite);
  const isSiteAllCameras = Boolean(
    selectedSite && selectedGroup && parseSiteScopeKey(selectedGroup) === selectedSite,
  );
  const isBuildingAllCameras = Boolean(
    selectedGroup && parseBuildingScopeKey(selectedGroup),
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
    if (!selectedBuildingKey || isSiteAllCameras || isBuildingAllCameras || !onlyFloorGroup) return;
    if (selectedGroup !== onlyFloorGroup) {
      onSelectGroup(onlyFloorGroup);
    }
  }, [
    selectedBuildingKey,
    isSiteAllCameras,
    isBuildingAllCameras,
    onlyFloorGroup,
    selectedGroup,
    onSelectGroup,
  ]);

  const selectedFloor =
    selectedGroup &&
    !parseSiteScopeKey(selectedGroup) &&
    !selectedGroup.startsWith('__building__:')
      ? floorGroups.find((fg) => fg.camera_group === selectedGroup)
      : undefined;

  const buildingCameraCount = floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);
  const buildingAllKey =
    parsedBuilding && buildingCameraCount > 0
      ? buildingScopeKey(parsedBuilding.site, parsedBuilding.building)
      : null;
  const hasFloorSubcategories = floorGroups.length > 1;

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
    if (buildingAllKey && value === buildingAllKey && parsedBuilding) {
      if (!confirmBulkLoad(
        `Load all cameras in ${parsedBuilding.building}?`,
        buildingCameraCount,
      )) {
        return;
      }
    }
    onSelectGroup(value);
  };

  const buildingSelectValue = isSiteAllCameras
    ? siteScopeKey(selectedSite!)
    : (selectedBuildingKey ?? '');

  const floorValue = (() => {
    if (!selectedGroup || parseSiteScopeKey(selectedGroup)) return NO_FLOOR_SELECTED;
    if (isBuildingAllCameras && buildingAllKey) return buildingAllKey;
    return selectedGroup;
  })();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:flex lg:flex-row gap-2 sm:gap-3 items-stretch lg:items-center">
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

      {selectedBuildingKey && !isSiteAllCameras && hasFloorSubcategories && (
        <div className="flex items-center gap-2 min-w-0">
          <Layers size={18} className="text-sky-400 shrink-0" />
          <label className="sr-only">Floor / Zone</label>
          <select
            value={floorValue}
            onChange={(e) => handleFloorChange(e.target.value)}
            className={selectClass}
          >
            <option value={NO_FLOOR_SELECTED}>Select floor / zone…</option>
            {buildingAllKey && parsedBuilding && (
              <option value={buildingAllKey}>
                All cameras — {parsedBuilding.building} ({buildingCameraCount} camera
                {buildingCameraCount === 1 ? '' : 's'})
              </option>
            )}
            {floorGroups.map((fg) => (
              <option key={fg.camera_group} value={fg.camera_group}>
                {fg.floor_group || fg.floor} — {fg.cameraCount} camera
                {fg.cameraCount === 1 ? '' : 's'}
              </option>
            ))}
          </select>
        </div>
      )}

      {selectedBuildingKey && !isSiteAllCameras && !hasFloorSubcategories && floorGroups.length === 1 && floorGroups[0] && (
        <div className="flex items-center gap-2 min-w-0">
          <Layers size={18} className="text-sky-400 shrink-0" />
          <span className={`${selectClass} inline-flex items-center bg-gray-100 dark:bg-gray-800/80 text-gray-700 dark:text-gray-300 cursor-default`}>
            {floorGroups[0].floor_group || floorGroups[0].floor}
            {' — '}
            {floorGroups[0].cameraCount ?? 0} camera
            {(floorGroups[0].cameraCount ?? 0) === 1 ? '' : 's'}
          </span>
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
