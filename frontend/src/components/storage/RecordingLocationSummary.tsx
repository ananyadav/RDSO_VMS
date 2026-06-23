import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Video } from 'lucide-react';
import Card from '../Card';

export interface LocationFloorRow {
  floor: string;
  total: number;
  recording: number;
}

export interface LocationBuildingRow {
  site: string;
  building: string;
  total: number;
  recording: number;
  floors: LocationFloorRow[];
}

export interface LocationSiteRow {
  site: string;
  total: number;
  recording: number;
  buildings: LocationBuildingRow[];
}

interface RecordingLocationSummaryProps {
  sites: LocationSiteRow[];
}

function StatusBar({ recording, total }: { recording: number; total: number }) {
  const pct = total > 0 ? Math.round((recording / total) * 100) : 0;
  return (
    <div className="flex items-center gap-2 min-w-[8rem]">
      <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${recording > 0 ? 'bg-emerald-500' : 'bg-gray-600'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs text-gray-400 whitespace-nowrap">
        {recording}/{total}
      </span>
    </div>
  );
}

export default function RecordingLocationSummary({ sites }: RecordingLocationSummaryProps) {
  const [openSites, setOpenSites] = useState<Record<string, boolean>>({});
  const [openBuildings, setOpenBuildings] = useState<Record<string, boolean>>({});

  if (!sites.length) return null;

  return (
    <Card>
      <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-700">
        <Video size={18} className="text-gray-400" />
        <h3 className="text-sm font-semibold text-white">Recording by location</h3>
        <span className="text-xs text-gray-500">building / floor — cameras recording now</span>
      </div>
      <div className="space-y-1 text-sm">
        {sites.map((site) => {
          const siteKey = site.site;
          const siteOpen = openSites[siteKey] ?? true;
          return (
            <div key={siteKey} className="rounded-md border border-gray-700/60 overflow-hidden">
              <button
                type="button"
                onClick={() => setOpenSites((p) => ({ ...p, [siteKey]: !siteOpen }))}
                className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800/50 hover:bg-gray-800 text-left"
              >
                {siteOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="font-medium text-gray-100 flex-1">{site.site}</span>
                <StatusBar recording={site.recording} total={site.total} />
              </button>
              {siteOpen && (
                <div className="px-2 py-1 space-y-1">
                  {site.buildings.map((b) => {
                    const bkey = `${siteKey}::${b.building}`;
                    const bOpen = openBuildings[bkey] ?? false;
                    return (
                      <div key={bkey} className="ml-4">
                        <button
                          type="button"
                          onClick={() => setOpenBuildings((p) => ({ ...p, [bkey]: !bOpen }))}
                          className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-800/40 text-left"
                        >
                          {bOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                          <span className="text-gray-200 flex-1">{b.building}</span>
                          <StatusBar recording={b.recording} total={b.total} />
                        </button>
                        {bOpen && (
                          <div className="ml-6 mb-1 space-y-0.5">
                            {b.floors.map((f) => (
                              <div
                                key={`${bkey}::${f.floor}`}
                                className="flex items-center gap-2 px-2 py-1 text-gray-400"
                              >
                                <span className="flex-1">{f.floor}</span>
                                <StatusBar recording={f.recording} total={f.total} />
                              </div>
                            ))}
                          </div>
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
    </Card>
  );
}
