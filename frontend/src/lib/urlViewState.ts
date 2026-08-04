import {
  buildingScopeKey,
  parseBuildingScopeKey,
  parseSiteScopeKey,
  ALL_CAMERAS_GROUP,
  type BuildingGroup,
} from '../constants/corporateFloors';
import { parseBuildingKey, buildingKey as toBuildingKey } from './cameraAccess';

export function isKnownCameraGroup(
  buildings: BuildingGroup[],
  group: string,
): boolean {
  if (parseSiteScopeKey(group)) return true;
  if (parseBuildingScopeKey(group)) return true;
  for (const b of buildings) {
    if (buildingScopeKey(b.site, b.building) === group) return true;
    for (const fg of b.floorGroups) {
      if (fg.camera_group === group) return true;
    }
  }
  return false;
}

export function resolveLiveViewFromUrl(
  params: URLSearchParams,
  buildings: BuildingGroup[],
): {
  site: string | null;
  buildingKey: string | null;
  group: string | null;
} | null {
  const site = params.get('site');
  const buildingKey = params.get('building');
  const group = params.get('group');

  if (!site && !buildingKey && !group) return null;

  if (group && !isKnownCameraGroup(buildings, group)) {
    return null;
  }

  const buildingScope = group ? parseBuildingScopeKey(group) : null;
  const resolvedBuildingKey =
    buildingKey ||
    (buildingScope ? toBuildingKey(buildingScope.site, buildingScope.building) : null);
  const resolvedSite =
    site || (buildingScope ? buildingScope.site : null) || (parseSiteScopeKey(group || '') || null);

  if (resolvedBuildingKey) {
    const parsed = parseBuildingKey(resolvedBuildingKey);
    if (!parsed) return null;
    const exists = buildings.some(
      (b) => b.site === parsed.site && b.building === parsed.building,
    );
    if (!exists) return null;
  }

  if (resolvedSite && !buildings.some((b) => b.site === resolvedSite) && !parseSiteScopeKey(group || '')) {
    return null;
  }

  return {
    site: resolvedSite || null,
    buildingKey: resolvedBuildingKey || null,
    group: group || null,
  };
}

export function resolvePlaybackFromUrl(
  params: URLSearchParams,
  buildings: BuildingGroup[],
): { building: string | null; group: string | null } | null {
  const building = params.get('building');
  const group = params.get('group');
  if (!building && !group) return null;

  if (group && !isKnownCameraGroup(buildings, group)) {
    return null;
  }

  if (building && building !== ALL_CAMERAS_GROUP) {
    const scope = parseBuildingKey(building);
    if (scope) {
      const exists = buildings.some(
        (b) => b.site === scope.site && b.building === scope.building,
      );
      if (!exists) return null;
    } else if (!buildings.some((b) => b.building === building)) {
      return null;
    }
  }

  return { building: building || null, group: group || null };
}
