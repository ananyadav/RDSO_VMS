import React, { useState, useEffect, useMemo } from 'react';
import { Video, Save, ChevronDown, ChevronRight, MapPin, Circle } from 'lucide-react';
import Card from './Card';

interface ToggleSwitchProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  mixed?: boolean;
}

const ToggleSwitch = ({ enabled, onChange, disabled = false, mixed = false }: ToggleSwitchProps) => (
  <button
    type="button"
    onClick={() => onChange(!enabled)}
    disabled={disabled}
    title={mixed ? 'Partial — click to enable all' : undefined}
    className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800 ${
      mixed ? 'bg-blue-400' : enabled ? 'bg-blue-600' : 'bg-gray-600'
    } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
  >
    <span
      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
        mixed ? 'translate-x-2.5' : enabled ? 'translate-x-5' : 'translate-x-0'
      }`}
    />
  </button>
);

interface Camera {
  id: string;
  name: string;
  site?: string;
  building?: string;
  floor?: string;
}

interface FloorGroup {
  floor: string;
  cameras: Camera[];
}

interface BuildingGroup {
  site: string;
  building: string;
  floors: FloorGroup[];
  cameras: Camera[];
}

interface SiteGroup {
  site: string;
  buildings: BuildingGroup[];
  cameras: Camera[];
}

interface RecordingScheduleProps {
  cameras: Camera[];
  schedule: Record<string, boolean>;
  onSave: (newSchedule: Record<string, boolean>) => void;
  isRecordingEnabled: boolean;
  onToggleRecording: (enabled: boolean) => void;
}

type GroupState = 'all' | 'none' | 'mixed';

function groupState(ids: string[], schedule: Record<string, boolean>): GroupState {
  if (ids.length === 0) return 'none';
  const enabled = ids.filter((id) => schedule[id]).length;
  if (enabled === 0) return 'none';
  if (enabled === ids.length) return 'all';
  return 'mixed';
}

function groupCamerasByLocation(cameras: Camera[]): SiteGroup[] {
  const sites = new Map<string, Map<string, Map<string, Camera[]>>>();

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

  const result: SiteGroup[] = [];

  for (const siteName of [...sites.keys()].sort()) {
    const buildingsMap = sites.get(siteName)!;
    const buildings: BuildingGroup[] = [];
    const siteCameras: Camera[] = [];

    for (const buildingName of [...buildingsMap.keys()].sort()) {
      const floorsMap = buildingsMap.get(buildingName)!;
      const floors: FloorGroup[] = [];
      const buildingCameras: Camera[] = [];

      for (const floorName of [...floorsMap.keys()].sort()) {
        const floorCams = [...floorsMap.get(floorName)!].sort((a, b) =>
          a.name.localeCompare(b.name),
        );
        buildingCameras.push(...floorCams);
        floors.push({ floor: floorName, cameras: floorCams });
      }

      siteCameras.push(...buildingCameras);
      buildings.push({
        site: siteName,
        building: buildingName,
        floors,
        cameras: buildingCameras,
      });
    }

    result.push({ site: siteName, buildings, cameras: siteCameras });
  }

  return result;
}

function applyGroupToggle(
  ids: string[],
  schedule: Record<string, boolean>,
): Record<string, boolean> {
  const state = groupState(ids, schedule);
  const enable = state !== 'all';
  const next = { ...schedule };
  for (const id of ids) {
    next[id] = enable;
  }
  return next;
}

function EnabledCount({ enabled, total }: { enabled: number; total: number }) {
  return (
    <span className={`text-xs tabular-nums ${enabled > 0 ? 'text-emerald-400' : 'text-gray-500'}`}>
      {enabled}/{total}
    </span>
  );
}

export default function RecordingSchedule({
  cameras,
  schedule,
  onSave,
  isRecordingEnabled,
  onToggleRecording,
}: RecordingScheduleProps): React.ReactElement {
  const [localSchedule, setLocalSchedule] = useState(schedule);
  const [openSites, setOpenSites] = useState<Record<string, boolean>>({});
  const [openBuildings, setOpenBuildings] = useState<Record<string, boolean>>({});
  const [openFloors, setOpenFloors] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setLocalSchedule(schedule);
  }, [schedule]);

  const locationTree = useMemo(() => groupCamerasByLocation(cameras), [cameras]);

  const allIds = useMemo(() => cameras.map((c) => c.id), [cameras]);

  const setGroup = (ids: string[]) => {
    setLocalSchedule((prev) => applyGroupToggle(ids, prev));
  };

  const handleCameraToggle = (cameraId: string) => {
    setLocalSchedule((prev) => ({
      ...prev,
      [cameraId]: !prev[cameraId],
    }));
  };

  return (
    <Card>
      <div className="flex items-center justify-between pb-4 border-b border-gray-700">
        <div className="flex items-center">
          <Video size={18} className="mr-3 text-gray-400" />
          <h3 className="text-lg font-bold text-white">Camera Recording</h3>
        </div>
        <button onClick={() => onSave(localSchedule)} className="btn-primary flex items-center text-sm">
          <Save size={16} className="mr-2" />
          Save Changes
        </button>
      </div>

      <div
        className={`flex items-center justify-between py-4 px-4 -mx-4 sm:mx-0 sm:rounded-lg border-b sm:border ${
          isRecordingEnabled
            ? 'bg-emerald-500/10 border-emerald-500/30'
            : 'bg-gray-700/30 border-gray-600'
        }`}
      >
        <div className="flex items-center gap-3 min-w-0">
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wide shrink-0 ${
              isRecordingEnabled
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                : 'bg-gray-600/50 text-gray-300 border border-gray-500/50'
            }`}
          >
            <Circle
              size={8}
              className={isRecordingEnabled ? 'fill-emerald-400 text-emerald-400' : 'fill-gray-400 text-gray-400'}
            />
            {isRecordingEnabled ? 'Recording Active' : 'Recording Disabled'}
          </span>
          <p className="text-xs text-gray-500 hidden sm:block">
            {isRecordingEnabled
              ? 'Cameras on the schedule below are recording to disk'
              : 'Turn on to start recording scheduled cameras'}
          </p>
        </div>
        <ToggleSwitch enabled={isRecordingEnabled} onChange={onToggleRecording} />
      </div>

      <div className="pt-3">
        <div className="flex items-center gap-2 mb-3">
          <MapPin size={16} className="text-sky-400" />
          <span className="text-sm font-semibold text-white">Schedule by location</span>
          <span className="text-xs text-gray-500">Site → Building → Floor</span>
        </div>

        {locationTree.length === 0 ? (
          <p className="text-sm text-gray-500 py-6 text-center">No cameras configured</p>
        ) : (
          <div className="rounded-lg border border-gray-700/60 overflow-hidden divide-y divide-gray-700/60">
            {locationTree.map((site) => {
              const siteKey = site.site;
              const siteOpen = openSites[siteKey] ?? true;
              const siteIds = site.cameras.map((c) => c.id);
              const siteState = groupState(siteIds, localSchedule);
              const siteEnabled = siteIds.filter((id) => localSchedule[id]).length;

              return (
                <div key={siteKey}>
                  <div className="flex items-center gap-2 px-3 py-2.5 bg-gray-800/50">
                    <button
                      type="button"
                      onClick={() => setOpenSites((p) => ({ ...p, [siteKey]: !siteOpen }))}
                      className="flex items-center gap-2 flex-1 min-w-0 text-left"
                    >
                      {siteOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <span className="font-medium text-gray-100 truncate">{site.site}</span>
                      <EnabledCount enabled={siteEnabled} total={siteIds.length} />
                    </button>
                    <ToggleSwitch
                      enabled={siteState === 'all'}
                      mixed={siteState === 'mixed'}
                      onChange={() => setGroup(siteIds)}
                      disabled={!isRecordingEnabled}
                    />
                  </div>

                  {siteOpen &&
                    site.buildings.map((building) => {
                      const bkey = `${siteKey}::${building.building}`;
                      const bOpen = openBuildings[bkey] ?? true;
                      const buildingIds = building.cameras.map((c) => c.id);
                      const buildingState = groupState(buildingIds, localSchedule);
                      const buildingEnabled = buildingIds.filter((id) => localSchedule[id]).length;

                      return (
                        <div key={bkey} className="border-t border-gray-800/80">
                          <div className="flex items-center gap-2 pl-6 pr-3 py-2 hover:bg-gray-800/30">
                            <button
                              type="button"
                              onClick={() => setOpenBuildings((p) => ({ ...p, [bkey]: !bOpen }))}
                              className="flex items-center gap-2 flex-1 min-w-0 text-left"
                            >
                              {bOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                              <span className="font-medium text-gray-200 truncate">{building.building}</span>
                              <EnabledCount enabled={buildingEnabled} total={buildingIds.length} />
                            </button>
                            <ToggleSwitch
                              enabled={buildingState === 'all'}
                              mixed={buildingState === 'mixed'}
                              onChange={() => setGroup(buildingIds)}
                              disabled={!isRecordingEnabled}
                            />
                          </div>

                          {bOpen &&
                            building.floors.map((floor) => {
                              const fkey = `${bkey}::${floor.floor}`;
                              const fOpen = openFloors[fkey] ?? false;
                              const floorIds = floor.cameras.map((c) => c.id);
                              const floorState = groupState(floorIds, localSchedule);
                              const floorEnabled = floorIds.filter((id) => localSchedule[id]).length;

                              return (
                                <div key={fkey} className="border-t border-gray-800/50">
                                  <div className="flex items-center gap-2 pl-12 pr-3 py-2 hover:bg-gray-800/20">
                                    <button
                                      type="button"
                                      onClick={() => setOpenFloors((p) => ({ ...p, [fkey]: !fOpen }))}
                                      className="flex items-center gap-2 flex-1 min-w-0 text-left"
                                    >
                                      {fOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                                      <span className="text-gray-300 truncate">{floor.floor}</span>
                                      <EnabledCount enabled={floorEnabled} total={floorIds.length} />
                                    </button>
                                    <ToggleSwitch
                                      enabled={floorState === 'all'}
                                      mixed={floorState === 'mixed'}
                                      onChange={() => setGroup(floorIds)}
                                      disabled={!isRecordingEnabled}
                                    />
                                  </div>

                                  {fOpen && (
                                    <div className="bg-gray-900/30 border-t border-gray-800/40">
                                      {floor.cameras.map((camera) => (
                                        <div
                                          key={camera.id}
                                          className="flex items-center justify-between pl-16 pr-3 py-2 hover:bg-gray-800/20"
                                        >
                                          <span className="text-sm text-gray-400 truncate mr-2">
                                            {camera.name}
                                          </span>
                                          <ToggleSwitch
                                            enabled={localSchedule[camera.id] || false}
                                            onChange={() => handleCameraToggle(camera.id)}
                                            disabled={!isRecordingEnabled}
                                          />
                                        </div>
                                      ))}
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
        )}

        {cameras.length > 0 && (
          <p className="text-xs text-gray-500 mt-3">
            {allIds.filter((id) => localSchedule[id]).length} of {allIds.length} cameras scheduled.
            Expand a floor to adjust individual cameras.
          </p>
        )}
      </div>
    </Card>
  );
}
