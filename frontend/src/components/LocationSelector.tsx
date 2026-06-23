import React, { useMemo } from 'react';
import { Building2, Layers } from 'lucide-react';
import {
  ALL_CAMERAS_GROUP,
  buildingScopeKey,
  parseBuildingScopeKey,
  type BuildingGroup,
} from '../constants/corporateFloors';

export type { BuildingGroup };
export type FloorGroup = BuildingGroup['floorGroups'][number];

interface LocationSelectorProps {
  buildings: BuildingGroup[];
  selectedBuilding: string | null;
  selectedGroup: string | null;
  onSelectBuilding: (building: string) => void;
  onSelectGroup: (cameraGroup: string) => void;
  /** When false, hide the "All locations" option (restricted users). */
  allowAllLocations?: boolean;
}

const selectClass =
  'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-200 border border-gray-300 dark:border-gray-600 rounded px-3 py-1.5 text-sm min-w-[11rem]';

export default function LocationSelector({
  buildings,
  selectedBuilding,
  selectedGroup,
  onSelectBuilding,
  onSelectGroup,
  allowAllLocations = true,
}: LocationSelectorProps) {
  const totalCameras = useMemo(
    () =>
      buildings.reduce(
        (sum, b) => sum + b.floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0),
        0,
      ),
    [buildings],
  );

  const isAllLocations = allowAllLocations && selectedBuilding === ALL_CAMERAS_GROUP;
  const building =
    buildings.find((b) => b.building === selectedBuilding) ??
    (selectedBuilding && !isAllLocations
      ? buildings.find((b) => {
          const scope = parseBuildingScopeKey(selectedGroup ?? '');
          return scope
            ? b.building === scope.building && b.site === scope.site
            : false;
        })
      : undefined) ??
    buildings[0];

  const floorGroups = building?.floorGroups ?? [];
  const buildingTotal = floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);

  const selectedFloor =
    selectedGroup && selectedGroup !== ALL_CAMERAS_GROUP && !selectedGroup.startsWith('__building__:')
      ? floorGroups.find((fg) => fg.camera_group === selectedGroup) ?? floorGroups[0]
      : undefined;

  const locationHint =
    selectedGroup === ALL_CAMERAS_GROUP
      ? 'All locations'
      : parseBuildingScopeKey(selectedGroup ?? '')
        ? `${parseBuildingScopeKey(selectedGroup ?? '')!.site} / ${parseBuildingScopeKey(selectedGroup ?? '')!.building} (all floors)`
        : selectedFloor?.location_path;

  return (
    <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
      <div className="flex items-center gap-2 min-w-0">
        <Building2 size={18} className="text-emerald-400 shrink-0" />
        <label className="sr-only">Building</label>
        <select
          value={isAllLocations ? ALL_CAMERAS_GROUP : (selectedBuilding ?? building?.building ?? '')}
          onChange={(e) => onSelectBuilding(e.target.value)}
          className={selectClass}
        >
          {allowAllLocations && (
            <option value={ALL_CAMERAS_GROUP}>
              All locations — {totalCameras} camera{totalCameras === 1 ? '' : 's'}
            </option>
          )}
          {buildings.map((b) => {
            const count = b.floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);
            return (
              <option key={`${b.site}::${b.building}`} value={b.building}>
                {b.building} — {count} camera{count === 1 ? '' : 's'}
              </option>
            );
          })}
        </select>
      </div>

      {!isAllLocations && (
        <div className="flex items-center gap-2 min-w-0">
          <Layers size={18} className="text-sky-400 shrink-0" />
          <label className="sr-only">Floor</label>
          {floorGroups.length === 0 ? (
            <span className="text-sm text-gray-500">No floors available</span>
          ) : (
            <select
              value={selectedGroup ?? buildingScopeKey(building!.site, building!.building)}
              onChange={(e) => onSelectGroup(e.target.value)}
              className={selectClass}
            >
              <option value={buildingScopeKey(building!.site, building!.building)}>
                All floors — {buildingTotal} camera{buildingTotal === 1 ? '' : 's'}
              </option>
              {floorGroups.map((fg) => (
                <option key={fg.camera_group} value={fg.camera_group}>
                  {fg.floor_group} — {fg.cameraCount} camera{fg.cameraCount === 1 ? '' : 's'}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {locationHint && (
        <span className="text-xs text-gray-500 hidden lg:inline truncate max-w-xs">
          {locationHint}
        </span>
      )}
    </div>
  );
}
