/** Site → building → floor helpers (mirrors backend camera_locations). */

export const DEFAULT_SITE_NAME = 'RML - 6';
export const CORPORATE_OFFICE = 'Corporate Office';

export const CORPORATE_OFFICE_FLOORS: string[] = [
  'Ground Floor',
  '1st Floor',
  '2nd Floor',
  '3rd Floor',
  '4th Floor',
  '5th Floor',
  '6th Floor',
  '7th Floor',
];

export interface LocationFloor {
  name: string;
  is_active?: boolean;
  camera_count?: number;
}

export interface LocationSiteBuilding {
  id: string;
  name: string;
  is_active?: boolean;
  floors: LocationFloor[];
  camera_count?: number;
}

export interface LocationSite {
  id: string;
  name: string;
  is_active?: boolean;
  buildings: LocationSiteBuilding[];
  camera_count?: number;
}

export interface LocationBuilding {
  id: string;
  site: string;
  site_id: string;
  building: string;
  floors: string[];
  is_active?: boolean;
}

export interface FloorGroupOption {
  floor_group: string;
  floor: string;
  camera_group: string;
  location_path: string;
  cameraCount?: number;
}

export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '_')
    .replace(/^_|_$/g, '');
}

export function cameraGroupFor(site: string, building: string, floor: string): string {
  const s = slugify(site);
  const b = slugify(building);
  const f = slugify(floor);
  if (!s || !b || !f) return '';
  return `${s}_${b}_${f}`;
}

export function locationForBuildingFloor(
  site: string,
  building: string,
  floor: string,
  area = '',
) {
  const camera_group = cameraGroupFor(site, building, floor);
  const parts = [site, building, floor, area].filter(Boolean);
  return {
    site,
    building,
    floor,
    floor_group: floor,
    area,
    camera_group,
    location_path: parts.join(' / '),
  };
}

export function buildingsForSite(
  buildings: LocationBuilding[],
  site: string,
): LocationBuilding[] {
  const key = site.toLowerCase();
  return buildings.filter((b) => b.site.toLowerCase() === key);
}

export function floorsForBuilding(
  buildings: LocationBuilding[],
  building: string,
  site?: string,
): string[] {
  const match = buildings.find(
    (b) =>
      b.building.toLowerCase() === building.toLowerCase() &&
      (!site || b.site.toLowerCase() === site.toLowerCase()),
  );
  return match?.floors ?? [];
}

/** Configured floors/zones/areas for a building (empty = free-text entry in forms). */
export const zonesForBuilding = floorsForBuilding;

export function buildingDefFor(
  buildings: LocationBuilding[],
  building: string,
  site?: string,
): LocationBuilding | undefined {
  return buildings.find(
    (b) =>
      b.building.toLowerCase() === building.toLowerCase() &&
      (!site || b.site.toLowerCase() === site.toLowerCase()),
  );
}

export const ALL_CAMERAS_GROUP = '__all__';
export const ALL_BUILDING_PREFIX = '__building__:';
export const ALL_SITE_PREFIX = '__site__:';

export function siteScopeKey(site: string): string {
  return `${ALL_SITE_PREFIX}${site}`;
}

export function parseSiteScopeKey(key: string): string | null {
  if (!key.startsWith(ALL_SITE_PREFIX)) return null;
  const site = key.slice(ALL_SITE_PREFIX.length).trim();
  return site || null;
}

export function buildingScopeKey(site: string, building: string): string {
  return `${ALL_BUILDING_PREFIX}${site}::${building}`;
}

export function parseBuildingScopeKey(key: string): { site: string; building: string } | null {
  if (!key.startsWith(ALL_BUILDING_PREFIX)) return null;
  const rest = key.slice(ALL_BUILDING_PREFIX.length);
  const sep = rest.indexOf('::');
  if (sep < 0) return null;
  return { site: rest.slice(0, sep), building: rest.slice(sep + 2) };
}

export function preferredBuilding(buildings: BuildingGroup[]): BuildingGroup | undefined {
  if (!buildings.length) return undefined;
  const corporate = buildings.find(
    (b) => b.building.toLowerCase() === CORPORATE_OFFICE.toLowerCase(),
  );
  if (corporate) return corporate;
  return [...buildings].sort((a, b) => {
    const aCount = a.floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);
    const bCount = b.floorGroups.reduce((n, fg) => n + (fg.cameraCount ?? 0), 0);
    return bCount - aCount;
  })[0];
}

export interface BuildingGroup {
  site: string;
  building: string;
  floorGroups: FloorGroupOption[];
}

export function preferredFloorGroup(
  floorGroups: FloorGroupOption[],
): FloorGroupOption | undefined {
  if (!floorGroups.length) return undefined;
  const withCameras = floorGroups.filter((fg) => (fg.cameraCount ?? 0) > 0);
  if (withCameras.length) return withCameras[0];
  return floorGroups[0];
}

/** Active-only site tree for camera forms and dropdowns. */
export function activeLocationSites(sites: LocationSite[]): LocationSite[] {
  return sites
    .filter((s) => s.is_active !== false)
    .map((site) => ({
      ...site,
      buildings: (site.buildings || [])
        .filter((b) => b.is_active !== false)
        .map((b) => ({
          ...b,
          floors: (b.floors || []).filter((f) => f.is_active !== false),
        })),
    }));
}

export function siteNamesFromTree(sites: LocationSite[]): string[] {
  return activeLocationSites(sites).map((s) => s.name);
}

export function buildingsForSiteTree(sites: LocationSite[], siteName: string): LocationSiteBuilding[] {
  const site = activeLocationSites(sites).find(
    (s) => s.name.toLowerCase() === siteName.toLowerCase(),
  );
  return site?.buildings ?? [];
}

export function floorsForBuildingTree(
  sites: LocationSite[],
  siteName: string,
  buildingName: string,
): string[] {
  const building = buildingsForSiteTree(sites, siteName).find(
    (b) => b.name.toLowerCase() === buildingName.toLowerCase(),
  );
  return (building?.floors ?? []).map((f) => f.name);
}
