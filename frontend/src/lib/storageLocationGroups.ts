import type { CameraStorageRow } from '../hooks/useStorageDashboard';

export interface StorageFloorGroup {
  floor: string;
  storage_gb: number;
  segment_count: number;
  recording: number;
  total: number;
  cameras: CameraStorageRow[];
}

export interface StorageBuildingGroup {
  site: string;
  building: string;
  storage_gb: number;
  segment_count: number;
  recording: number;
  total: number;
  floors: StorageFloorGroup[];
}

export interface StorageSiteGroup {
  site: string;
  storage_gb: number;
  segment_count: number;
  recording: number;
  total: number;
  buildings: StorageBuildingGroup[];
}

function agg(cameras: CameraStorageRow[]) {
  return cameras.reduce(
    (acc, c) => ({
      storage_gb: acc.storage_gb + (c.storage_used_gb || 0),
      segment_count: acc.segment_count + (c.segment_count || 0),
      recording: acc.recording + (c.is_recording ? 1 : 0),
      total: acc.total + 1,
    }),
    { storage_gb: 0, segment_count: 0, recording: 0, total: 0 },
  );
}

export function groupStorageByLocation(cameras: CameraStorageRow[]): StorageSiteGroup[] {
  const sites = new Map<string, Map<string, Map<string, CameraStorageRow[]>>>();

  for (const cam of cameras) {
    const site = cam.site?.trim() || 'Unassigned';
    const building = cam.building?.trim() || 'Unassigned';
    const floor = cam.floor?.trim() || 'Unassigned';

    if (!sites.has(site)) sites.set(site, new Map());
    const buildings = sites.get(site)!;
    if (!buildings.has(building)) buildings.set(building, new Map());
    const floors = buildings.get(building)!;
    if (!floors.has(floor)) floors.set(floor, []);
    floors.get(floor)!.push(cam);
  }

  const result: StorageSiteGroup[] = [];

  for (const siteName of [...sites.keys()].sort()) {
    const buildingsMap = sites.get(siteName)!;
    const siteCameras: CameraStorageRow[] = [];
    const buildings: StorageBuildingGroup[] = [];

    for (const buildingName of [...buildingsMap.keys()].sort()) {
      const floorsMap = buildingsMap.get(buildingName)!;
      const buildingCameras: CameraStorageRow[] = [];
      const floors: StorageFloorGroup[] = [];

      for (const floorName of [...floorsMap.keys()].sort()) {
        const floorCams = [...floorsMap.get(floorName)!].sort((a, b) =>
          a.camera_name.localeCompare(b.camera_name),
        );
        buildingCameras.push(...floorCams);
        floors.push({
          floor: floorName,
          cameras: floorCams,
          ...agg(floorCams),
        });
      }

      siteCameras.push(...buildingCameras);
      buildings.push({
        site: siteName,
        building: buildingName,
        floors,
        ...agg(buildingCameras),
      });
    }

    result.push({
      site: siteName,
      buildings,
      ...agg(siteCameras),
    });
  }

  return result;
}
