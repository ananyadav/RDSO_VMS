import { authService } from '../services/authService';
import {
  ALL_CAMERAS_GROUP,
  buildingScopeKey,
  preferredBuilding,
  preferredFloorGroup,
  type BuildingGroup,
} from '../constants/corporateFloors';

export interface PublicCameraAccess {
  all?: boolean;
  allowedCameraGroups?: string[];
  allowedCameraUids?: string[];
}

export const NO_FLOOR_SELECTED = '__pick_floor__';

export function buildingKey(site: string, building: string): string {
  return `${site}::${building}`;
}

export function parseBuildingKey(key: string): { site: string; building: string } | null {
  const sep = key.indexOf('::');
  if (sep < 0) return null;
  return { site: key.slice(0, sep), building: key.slice(sep + 2) };
}

/** When a building has exactly one floor/zone, return its camera_group. */
export function soleFloorGroup(
  floorGroups: BuildingGroup['floorGroups'],
): string | null {
  if (floorGroups.length !== 1) return null;
  return floorGroups[0].camera_group || null;
}

/** True when user may see every camera (admin or legacy unrestricted). */
export function hasUnrestrictedCameraAccess(access?: PublicCameraAccess | null): boolean {
  if (!access) return true;
  if (access.all) return true;
  const groups = access.allowedCameraGroups ?? [];
  const uids = access.allowedCameraUids ?? [];
  return groups.length === 0 && uids.length === 0;
}

export function isAdminUser(): boolean {
  return (authService.getCurrentUser()?.role ?? '').trim().toLowerCase() === 'admin';
}

export function pickDefaultLocation(
  buildings: BuildingGroup[],
): { site: string; buildingKey: string; group: string } | null {
  const b = preferredBuilding(buildings);
  if (!b) return null;
  const fg = preferredFloorGroup(b.floorGroups);
  if (fg) {
    return {
      site: b.site,
      buildingKey: buildingKey(b.site, b.building),
      group: fg.camera_group,
    };
  }
  return {
    site: b.site,
    buildingKey: buildingKey(b.site, b.building),
    group: buildingScopeKey(b.site, b.building),
  };
}

/** Live View: never auto-load all cameras; floor must be chosen explicitly. */
export function initialLiveViewSelection(
  buildings: BuildingGroup[],
  _access?: PublicCameraAccess | null,
): { site: string | null; buildingKey: string | null; group: string | null } {
  if (!buildings.length) {
    return { site: null, buildingKey: null, group: null };
  }
  if (isAdminUser()) {
    return { site: null, buildingKey: null, group: null };
  }
  const b = preferredBuilding(buildings);
  if (!b) return { site: null, buildingKey: null, group: null };
  const key = buildingKey(b.site, b.building);
  return {
    site: b.site,
    buildingKey: key,
    group: soleFloorGroup(b.floorGroups),
  };
}

/** @deprecated Playback still uses all-locations default for unrestricted users. */
export function initialLocationSelection(
  buildings: BuildingGroup[],
  access?: PublicCameraAccess | null,
): { building: string; group: string } | null {
  if (hasUnrestrictedCameraAccess(access)) {
    return { building: ALL_CAMERAS_GROUP, group: ALL_CAMERAS_GROUP };
  }
  const picked = pickDefaultLocation(buildings);
  if (!picked) return null;
  return { building: picked.buildingKey.split('::')[1] ?? '', group: picked.group };
}
