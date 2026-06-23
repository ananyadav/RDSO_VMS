import React, { useMemo } from 'react';
import { Building2, ChevronDown, ChevronRight, Layers, MapPin } from 'lucide-react';

export interface LocationStats {
  total: number;
  active: number;
  disabled: number;
  online: number;
  offline: number;
  errors: number;
  recording?: number;
  liveConsumers?: number;
}

export interface FloorGroupNode {
  floor_group: string;
  floor: string;
  camera_group: string;
  location_path: string;
  cameraCount: number;
  stats?: LocationStats;
}

export interface BuildingNode {
  site: string;
  building: string;
  floorGroups: FloorGroupNode[];
  stats?: LocationStats;
}

interface LocationTreePanelProps {
  buildings: BuildingNode[];
  selectedBuilding: string | null;
  selectedGroup: string | null;
  onSelect: (building: string, cameraGroup: string) => void;
  loading?: boolean;
}

function hoverStats(stats?: LocationStats, fallback = 0): string {
  const s = stats ?? { total: fallback, active: fallback, disabled: 0, online: 0, offline: 0, errors: 0 };
  const parts = [
    `${s.total} total`,
    `${s.online} online`,
    `${s.offline} offline`,
    s.disabled ? `${s.disabled} disabled` : null,
    s.errors ? `${s.errors} errors` : null,
    s.recording ? `${s.recording} recording` : null,
  ].filter(Boolean);
  return parts.join(' · ');
}

function CountBadge({ count }: { count: number }) {
  return (
    <span className="shrink-0 min-w-[1.25rem] text-center text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded-md bg-sky-500/15 text-sky-300 ring-1 ring-sky-500/25">
      {count}
    </span>
  );
}

export default function LocationTreePanel({
  buildings,
  selectedBuilding,
  selectedGroup,
  onSelect,
  loading = false,
}: LocationTreePanelProps) {
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});

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

  React.useEffect(() => {
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
    return (
      <div className="p-3 text-xs text-gray-500 leading-relaxed">
        No cameras yet. Use <strong>Manage Locations</strong> to configure sites and floors.
      </div>
    );
  }

  return (
    <div className="overflow-y-auto p-1.5 space-y-0.5 text-sm">
      {sites.map(({ site, buildings: siteBuildings }) => {
        const siteOpen = expanded[site] !== false;
        const siteTotal = siteBuildings.reduce(
          (n, b) => n + (b.stats?.total ?? b.floorGroups.reduce((s, fg) => s + (fg.stats?.total ?? fg.cameraCount), 0)),
          0,
        );
        return (
          <div key={site}>
            <button
              type="button"
              className="group w-full flex items-center gap-1.5 px-2 py-1.5 rounded-md text-left hover:bg-gray-100 dark:hover:bg-gray-800/80"
              title={hoverStats(undefined, siteTotal)}
              onClick={() => setExpanded((e) => ({ ...e, [site]: !siteOpen }))}
            >
              {siteOpen ? <ChevronDown size={14} className="shrink-0 text-gray-500" /> : <ChevronRight size={14} className="shrink-0 text-gray-500" />}
              <MapPin size={14} className="shrink-0 text-violet-400" />
              <span className="flex-1 truncate font-semibold text-gray-800 dark:text-gray-100">{site}</span>
              <CountBadge count={siteTotal} />
            </button>
            {siteOpen && (
              <div className="ml-2 border-l border-gray-200 dark:border-gray-700/80 pl-1">
                {siteBuildings.map((b) => {
                  const bKey = `${site}::${b.building}`;
                  const isOpen = expanded[bKey] !== false;
                  const bTotal = b.stats?.total ?? b.floorGroups.reduce((s, fg) => s + (fg.stats?.total ?? fg.cameraCount), 0);
                  return (
                    <div key={bKey}>
                      <button
                        type="button"
                        className="group w-full flex items-center gap-1.5 px-2 py-1 rounded-md text-left hover:bg-gray-100 dark:hover:bg-gray-800/80"
                        title={hoverStats(b.stats, bTotal)}
                        onClick={() => setExpanded((e) => ({ ...e, [bKey]: !isOpen }))}
                      >
                        {isOpen ? <ChevronDown size={13} className="shrink-0 text-gray-500" /> : <ChevronRight size={13} className="shrink-0 text-gray-500" />}
                        <Building2 size={13} className="shrink-0 text-emerald-400" />
                        <span className="flex-1 truncate font-medium text-gray-700 dark:text-gray-200">{b.building}</span>
                        <CountBadge count={bTotal} />
                      </button>
                      {isOpen && (
                        <ul className="ml-4 mb-1 space-y-0.5">
                          {b.floorGroups.map((fg) => {
                            const selected = selectedGroup === fg.camera_group;
                            const count = fg.stats?.total ?? fg.cameraCount;
                            return (
                              <li key={fg.camera_group}>
                                <button
                                  type="button"
                                  title={hoverStats(fg.stats, count)}
                                  className={`group w-full flex items-center gap-1.5 px-2 py-1 rounded-md text-left transition-colors ${
                                    selected
                                      ? 'bg-sky-500/15 text-sky-100 ring-1 ring-sky-500/40'
                                      : 'hover:bg-gray-100 dark:hover:bg-gray-800/80 text-gray-600 dark:text-gray-300'
                                  }`}
                                  onClick={() => onSelect(b.building, fg.camera_group)}
                                >
                                  <Layers size={12} className={`shrink-0 ${selected ? 'text-sky-400' : 'text-sky-500/70'}`} />
                                  <span className="flex-1 truncate text-xs font-medium">
                                    {fg.floor_group || fg.floor}
                                  </span>
                                  <CountBadge count={count} />
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
