import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Activity, Loader2, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import Card from './Card';
import type { CameraStorageRow } from '../hooks/useStorageDashboard';
import { apiFetch } from '../lib/api';

interface HealthCamera {
  camera_id: string;
  camera_name: string;
  recording_status: string;
  ffmpeg_status: string;
  health: string;
  health_label: string;
  last_segment_time: string | null;
  last_recording_time: string | null;
  segment_count: number;
}

interface HealthData {
  updated_at: string;
  summary: {
    total: number;
    recording: number;
    healthy: number;
    warning: number;
    reconnecting: number;
    offline: number;
    idle: number;
  };
  cameras: HealthCamera[];
}

interface LocationMeta {
  site: string;
  building: string;
  floor: string;
}

interface HealthFloorGroup {
  floor: string;
  cameras: HealthCamera[];
  recording: number;
  total: number;
}

interface HealthBuildingGroup {
  building: string;
  floors: HealthFloorGroup[];
  recording: number;
  total: number;
}

interface HealthSiteGroup {
  site: string;
  buildings: HealthBuildingGroup[];
  recording: number;
  total: number;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const HEALTH_STYLES: Record<string, string> = {
  healthy: 'bg-green-500/20 text-green-400 border-green-500/30',
  warning: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  reconnecting: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  offline: 'bg-red-500/20 text-red-400 border-red-500/30',
  idle: 'bg-gray-600/40 text-gray-400 border-gray-600/50',
};

function isRecording(cam: HealthCamera): boolean {
  return cam.recording_status === 'Recording';
}

function groupHealthByLocation(
  cameras: HealthCamera[],
  locationById: Map<string, LocationMeta>,
): HealthSiteGroup[] {
  const sites = new Map<string, Map<string, Map<string, HealthCamera[]>>>();

  for (const cam of cameras) {
    const loc = locationById.get(cam.camera_id);
    const site = loc?.site?.trim() || 'Unassigned';
    const building = loc?.building?.trim() || 'Unassigned';
    const floor = loc?.floor?.trim() || 'Unassigned';

    if (!sites.has(site)) sites.set(site, new Map());
    const buildings = sites.get(site)!;
    if (!buildings.has(building)) buildings.set(building, new Map());
    const floors = buildings.get(building)!;
    if (!floors.has(floor)) floors.set(floor, []);
    floors.get(floor)!.push(cam);
  }

  const result: HealthSiteGroup[] = [];

  for (const siteName of [...sites.keys()].sort()) {
    const buildingsMap = sites.get(siteName)!;
    const buildings: HealthBuildingGroup[] = [];
    let siteRecording = 0;
    let siteTotal = 0;

    for (const buildingName of [...buildingsMap.keys()].sort()) {
      const floorsMap = buildingsMap.get(buildingName)!;
      const floors: HealthFloorGroup[] = [];
      let buildingRecording = 0;
      let buildingTotal = 0;

      for (const floorName of [...floorsMap.keys()].sort()) {
        const floorCams = [...floorsMap.get(floorName)!].sort((a, b) =>
          a.camera_name.localeCompare(b.camera_name),
        );
        const floorRecording = floorCams.filter(isRecording).length;
        floors.push({
          floor: floorName,
          cameras: floorCams,
          recording: floorRecording,
          total: floorCams.length,
        });
        buildingRecording += floorRecording;
        buildingTotal += floorCams.length;
      }

      buildings.push({
        building: buildingName,
        floors,
        recording: buildingRecording,
        total: buildingTotal,
      });
      siteRecording += buildingRecording;
      siteTotal += buildingTotal;
    }

    result.push({
      site: siteName,
      buildings,
      recording: siteRecording,
      total: siteTotal,
    });
  }

  return result;
}

function HealthBadge({ health, label }: { health: string; label: string }) {
  return (
    <span
      className={`inline-flex px-2 py-0.5 rounded border text-xs font-medium ${
        HEALTH_STYLES[health] ?? HEALTH_STYLES.idle
      }`}
    >
      {label}
    </span>
  );
}

function CameraHealthRows({ cameras }: { cameras: HealthCamera[] }) {
  return (
    <>
      {cameras.map((cam) => (
        <tr key={cam.camera_id} className="hover:bg-gray-700/30">
          <td className="px-4 py-2 font-medium text-white pl-8">{cam.camera_name}</td>
          <td className="px-4 py-2 text-gray-300">{cam.recording_status}</td>
          <td className="px-4 py-2 text-gray-400">{cam.ffmpeg_status}</td>
          <td className="px-4 py-2">
            <HealthBadge health={cam.health} label={cam.health_label} />
          </td>
          <td className="px-4 py-2 text-gray-400 text-xs whitespace-nowrap">
            {formatTime(cam.last_segment_time)}
          </td>
          <td className="px-4 py-2 text-gray-400 text-xs whitespace-nowrap">
            {formatTime(cam.last_recording_time)}
          </td>
        </tr>
      ))}
    </>
  );
}

function LocationHealthTree({ sites }: { sites: HealthSiteGroup[] }) {
  const [openSites, setOpenSites] = useState<Record<string, boolean>>({});
  const [openBuildings, setOpenBuildings] = useState<Record<string, boolean>>({});
  const [openFloors, setOpenFloors] = useState<Record<string, boolean>>({});

  return (
    <div className="divide-y divide-gray-700/60">
      {sites.map((site) => {
        const siteKey = site.site;
        const siteOpen = openSites[siteKey] ?? true;
        return (
          <div key={siteKey}>
            <button
              type="button"
              onClick={() => setOpenSites((p) => ({ ...p, [siteKey]: !siteOpen }))}
              className="w-full flex items-center gap-2 px-4 py-3 bg-gray-800/40 hover:bg-gray-800/70 text-left"
            >
              {siteOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              <span className="font-semibold text-white">{site.site}</span>
              <span className="text-xs text-gray-400 ml-auto">
                {site.recording}/{site.total} recording
              </span>
            </button>

            {siteOpen &&
              site.buildings.map((building) => {
                const bkey = `${siteKey}::${building.building}`;
                const bOpen = openBuildings[bkey] ?? false;
                return (
                  <div key={bkey} className="border-t border-gray-800/80">
                    <button
                      type="button"
                      onClick={() => setOpenBuildings((p) => ({ ...p, [bkey]: !bOpen }))}
                      className="w-full flex items-center gap-2 pl-8 pr-4 py-2.5 hover:bg-gray-800/30 text-left"
                    >
                      {bOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <span className="font-medium text-gray-200">{building.building}</span>
                      <span className="text-xs text-gray-400 ml-auto">
                        {building.recording}/{building.total} recording
                      </span>
                    </button>

                    {bOpen &&
                      building.floors.map((floor) => {
                        const fkey = `${bkey}::${floor.floor}`;
                        const fOpen = openFloors[fkey] ?? false;
                        return (
                          <div key={fkey}>
                            <button
                              type="button"
                              onClick={() => setOpenFloors((p) => ({ ...p, [fkey]: !fOpen }))}
                              className="w-full flex items-center gap-2 pl-14 pr-4 py-2 hover:bg-gray-800/20 text-left"
                            >
                              {fOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                              <span className="text-gray-300">{floor.floor}</span>
                              <span className="text-xs text-gray-400 ml-auto">
                                {floor.recording}/{floor.total} recording
                              </span>
                            </button>

                            {fOpen && (
                              <div className="overflow-x-auto border-t border-gray-800/60">
                                <table className="w-full text-sm text-left">
                                  <thead className="text-xs text-gray-500 uppercase bg-gray-900/40">
                                    <tr>
                                      <th className="px-4 py-2 pl-8">Camera</th>
                                      <th className="px-4 py-2">Recording</th>
                                      <th className="px-4 py-2">FFmpeg</th>
                                      <th className="px-4 py-2">Health</th>
                                      <th className="px-4 py-2">Last Segment</th>
                                      <th className="px-4 py-2">Last Recording</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-gray-800/40">
                                    <CameraHealthRows cameras={floor.cameras} />
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                );
              })}
          </div>
        );
      })}
    </div>
  );
}

interface RecordingHealthMonitorProps {
  locationCameras?: CameraStorageRow[];
}

export default function RecordingHealthMonitor({
  locationCameras,
}: RecordingHealthMonitorProps): React.ReactElement {
  const [data, setData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await apiFetch('/api/recordings/health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load health');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 15000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  const locationById = useMemo(() => {
    const map = new Map<string, LocationMeta>();
    for (const cam of locationCameras ?? []) {
      map.set(cam.camera_id, {
        site: cam.site || 'Unassigned',
        building: cam.building || 'Unassigned',
        floor: cam.floor || 'Unassigned',
      });
    }
    return map;
  }, [locationCameras]);

  const locationTree = useMemo(() => {
    if (!data?.cameras.length || locationById.size === 0) return null;
    return groupHealthByLocation(data.cameras, locationById);
  }, [data, locationById]);

  if (loading && !data) {
    return (
      <Card className="flex items-center justify-center py-8 text-gray-400">
        <Loader2 className="animate-spin mr-2" size={18} />
        Loading recording health…
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card className="flex items-center gap-2 py-6 text-red-400">
        <AlertTriangle size={18} />
        {error}
      </Card>
    );
  }

  if (!data) return <></>;

  const { summary, cameras } = data;

  return (
    <div className="w-full">
      <Card className="overflow-hidden p-0 w-full">
        <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Activity size={18} className="text-blue-400" />
            <h4 className="text-sm font-semibold text-white">Recording Health Monitor</h4>
            {locationTree && (
              <span className="text-xs text-gray-500">Site → Building → Floor → Cameras</span>
            )}
          </div>
          <div className="flex gap-3 text-xs text-gray-500">
            <span className="text-green-400">{summary.healthy} healthy</span>
            <span className="text-amber-400">{summary.reconnecting} reconnecting</span>
            <span className="text-yellow-400">{summary.warning} warning</span>
            <span className="text-gray-400">{summary.idle} idle</span>
          </div>
        </div>

        {locationTree ? (
          <div className="max-h-[min(520px,55vh)] overflow-y-auto">
            <LocationHealthTree sites={locationTree} />
          </div>
        ) : (
          <div className="overflow-x-auto max-h-[min(480px,55vh)] overflow-y-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-xs text-gray-400 uppercase bg-gray-800/80 sticky top-0 z-10">
                <tr>
                  <th className="px-4 py-3">Camera</th>
                  <th className="px-4 py-3">Recording</th>
                  <th className="px-4 py-3">FFmpeg</th>
                  <th className="px-4 py-3">Health</th>
                  <th className="px-4 py-3">Last Segment</th>
                  <th className="px-4 py-3">Last Recording</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700/60">
                {cameras.map((cam) => (
                  <tr key={cam.camera_id} className="hover:bg-gray-700/30">
                    <td className="px-4 py-3 font-medium text-white">{cam.camera_name}</td>
                    <td className="px-4 py-3 text-gray-300">{cam.recording_status}</td>
                    <td className="px-4 py-3 text-gray-400">{cam.ffmpeg_status}</td>
                    <td className="px-4 py-3">
                      <HealthBadge health={cam.health} label={cam.health_label} />
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                      {formatTime(cam.last_segment_time)}
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                      {formatTime(cam.last_recording_time)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
