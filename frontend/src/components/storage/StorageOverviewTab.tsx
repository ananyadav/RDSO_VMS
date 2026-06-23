import React, { useMemo, useState } from 'react';
import { HardDrive, Database, Clock, ChevronDown, ChevronRight, MapPin } from 'lucide-react';
import Card from '../Card';
import SummaryCard from './SummaryCard';
import StorageDrivesTab from './StorageDrivesTab';
import {
  StorageDashboardData,
  CameraStorageRow,
  formatStorageTime,
  diskFreePercent,
  diskStatusLevel,
  diskStatusLabel,
} from '../../hooks/useStorageDashboard';
import { groupStorageByLocation } from '../../lib/storageLocationGroups';

interface StorageOverviewTabProps {
  data: StorageDashboardData;
}

function CameraRows({ cameras }: { cameras: CameraStorageRow[] }) {
  if (cameras.length === 0) {
    return (
      <tr>
        <td colSpan={6} className="px-4 py-4 text-center text-gray-500 text-sm">
          No cameras in this location
        </td>
      </tr>
    );
  }

  return (
    <>
      {cameras.map((cam) => (
        <tr key={cam.camera_id} className="hover:bg-gray-700/30">
          <td className="px-4 py-2 font-medium text-white pl-8">{cam.camera_name}</td>
          <td className="px-4 py-2 text-right text-gray-200 tabular-nums">
            {cam.storage_used_gb.toFixed(3)} GB
          </td>
          <td className="px-4 py-2 text-right text-gray-400 tabular-nums">{cam.segment_count}</td>
          <td className="px-4 py-2 text-gray-400 text-xs whitespace-nowrap">
            {formatStorageTime(cam.latest_segment_time)}
          </td>
          <td className="px-4 py-2 text-right text-gray-400 tabular-nums">
            {cam.gb_per_day_estimate != null ? cam.gb_per_day_estimate.toFixed(2) : '—'}
          </td>
          <td className="px-4 py-2 text-right tabular-nums">
            {cam.estimated_days_remaining != null ? (
              <span className={cam.estimated_days_remaining < 7 ? 'text-red-400' : 'text-gray-300'}>
                {cam.estimated_days_remaining}
              </span>
            ) : (
              '—'
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

function LevelSummary({
  storage_gb,
  segment_count,
  total,
}: {
  label: string;
  storage_gb: number;
  segment_count: number;
  recording: number;
  total: number;
}) {
  return (
    <span className="text-xs text-gray-400 ml-auto flex flex-wrap gap-x-3 gap-y-0.5">
      <span>{storage_gb.toFixed(2)} GB</span>
      <span>{segment_count} seg</span>
      <span>{total} cam{total !== 1 ? 's' : ''}</span>
    </span>
  );
}

export default function StorageOverviewTab({ data }: StorageOverviewTabProps) {
  const { disk, summary, cameras } = data;
  const freePct = diskFreePercent(disk);
  const diskLevel = diskStatusLevel(disk);
  const daysLevel =
    summary.estimated_days_remaining != null && summary.estimated_days_remaining < 7
      ? 'red'
      : summary.estimated_days_remaining != null && summary.estimated_days_remaining < 14
        ? 'yellow'
        : undefined;

  const locationTree = useMemo(() => groupStorageByLocation(cameras), [cameras]);
  const [openSites, setOpenSites] = useState<Record<string, boolean>>({});
  const [openBuildings, setOpenBuildings] = useState<Record<string, boolean>>({});
  const [openFloors, setOpenFloors] = useState<Record<string, boolean>>({});

  return (
    <div className="space-y-4 w-full">
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <SummaryCard
          icon={<Database size={20} />}
          title="Recordings Used"
          value={`${summary.recordings_storage_gb.toFixed(2)} GB`}
          sub={`${summary.total_segments} segments · ${summary.camera_count} cameras`}
        />
        <SummaryCard
          icon={<HardDrive size={20} />}
          title="Free Disk Space"
          value={`${disk.disk_free_gb} GB`}
          sub={`${freePct}% free · ${diskStatusLabel(disk)}`}
          level={diskLevel}
        />
        <SummaryCard
          icon={<Clock size={20} />}
          title="Est. Days Remaining"
          value={
            summary.estimated_days_remaining != null
              ? `${summary.estimated_days_remaining} days`
              : '—'
          }
          sub={
            summary.days_remaining_formula
              ? `${summary.days_remaining_formula} = ${summary.estimated_days_remaining} days`
              : summary.combined_gb_per_day
                ? `${disk.disk_free_gb} GB ÷ ${summary.combined_gb_per_day} GB/day`
                : 'No growth rate data yet'
          }
          level={daysLevel}
        />
        <SummaryCard
          icon={<Database size={20} />}
          title="Storage Growth"
          value={
            summary.combined_gb_per_day != null
              ? `~${summary.combined_gb_per_day} GB/day`
              : '—'
          }
          sub={`${summary.camera_count} cameras with footage on disk`}
        />
      </div>

      <StorageDrivesTab data={data} />

      <Card className="overflow-hidden p-0">
        <div className="px-4 py-3 border-b border-gray-700 flex items-center gap-2">
          <MapPin size={16} className="text-sky-400" />
          <h4 className="text-sm font-semibold text-white">Storage by Location</h4>
          <span className="text-xs text-gray-500">Site → Building → Floor → Cameras</span>
        </div>

        {locationTree.length === 0 ? (
          <p className="px-4 py-8 text-center text-gray-500">No recording data on disk yet</p>
        ) : (
          <div className="divide-y divide-gray-700/60">
            {locationTree.map((site) => {
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
                    <LevelSummary {...site} label={site.site} />
                  </button>

                  {siteOpen && site.buildings.map((building) => {
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
                          <LevelSummary {...building} label={building.building} />
                        </button>

                        {bOpen && building.floors.map((floor) => {
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
                                <LevelSummary {...floor} label={floor.floor} />
                              </button>

                              {fOpen && (
                                <div className="overflow-x-auto border-t border-gray-800/60">
                                  <table className="w-full text-sm text-left">
                                    <thead className="text-xs text-gray-500 uppercase bg-gray-900/40">
                                      <tr>
                                        <th className="px-4 py-2 pl-8">Camera</th>
                                        <th className="px-4 py-2 text-right">Storage</th>
                                        <th className="px-4 py-2 text-right">Segments</th>
                                        <th className="px-4 py-2">Last Recording</th>
                                        <th className="px-4 py-2 text-right">GB/day</th>
                                        <th className="px-4 py-2 text-right">Days Left</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-800/40">
                                      <CameraRows cameras={floor.cameras} />
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
        )}
      </Card>
    </div>
  );
}
