import React, { useState, useEffect } from 'react';
import { HardDrive } from 'lucide-react';
import Card from './Card';

interface DriveInfo {
  name: string;
  total_gb: number;
  used_gb: number;
  free_gb: number;
  free_percent: number;
  percent: number;
  status_level: 'green' | 'yellow' | 'red';
  status_label: string;
}

const STATUS_COLORS = {
  green: { badge: 'text-green-400', bar: 'bg-green-500' },
  yellow: { badge: 'text-yellow-400', bar: 'bg-yellow-500' },
  red: { badge: 'text-red-400', bar: 'bg-red-500' },
};

export default function StorageDrives(): React.ReactElement {
  const [drives, setDrives] = useState<DriveInfo[]>([]);

  useEffect(() => {
    const fetchDrives = async () => {
      try {
        const res = await fetch('/api/storage/dashboard');
        if (!res.ok) return;
        const data = await res.json();
        const disk = data.disk;
        if (disk?.disk_total_gb != null) {
          const freePct =
            disk.disk_free_percent > 0
              ? disk.disk_free_percent
              : Math.round((disk.disk_free_gb / disk.disk_total_gb) * 1000) / 10;
          const level: 'green' | 'yellow' | 'red' =
            disk.status_level ||
            (freePct > 20 ? 'green' : freePct > 10 ? 'yellow' : 'red');
          const label =
            disk.status_label ||
            (level === 'green' ? 'Healthy' : level === 'yellow' ? 'Low' : 'Critical');
          setDrives([
            {
              name: 'Recordings volume',
              total_gb: disk.disk_total_gb,
              used_gb: disk.disk_used_gb,
              free_gb: disk.disk_free_gb,
              free_percent: freePct,
              percent: disk.disk_percent,
              status_level: level,
              status_label: label,
            },
          ]);
        }
      } catch {
        // fallback to /api/status
        try {
          const res = await fetch('/api/status');
          if (!res.ok) return;
          const data = await res.json();
          if (data.disk_total_gb != null) {
            const free = data.disk_total_gb - data.disk_used_gb;
            const freePct = (free / data.disk_total_gb) * 100;
            const level = freePct > 20 ? 'green' : freePct > 10 ? 'yellow' : 'red';
            setDrives([
              {
                name: 'System Disk',
                total_gb: data.disk_total_gb,
                used_gb: data.disk_used_gb,
                free_gb: parseFloat(free.toFixed(1)),
                free_percent: parseFloat(freePct.toFixed(1)),
                percent: data.disk_percent,
                status_level: level,
                status_label: level === 'green' ? 'Healthy' : level === 'yellow' ? 'Low' : 'Critical',
              },
            ]);
          }
        } catch {
          /* empty */
        }
      }
    };
    fetchDrives();
    const interval = setInterval(fetchDrives, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <Card>
      <div className="flex items-center mb-4">
        <HardDrive size={18} className="mr-3 text-gray-400" />
        <h3 className="text-lg font-bold text-white">Storage Drives</h3>
      </div>
      {drives.length === 0 ? (
        <p className="text-sm text-gray-500">Loading drive info…</p>
      ) : (
        <div className="space-y-4">
          {drives.map((drive, i) => {
            const colors = STATUS_COLORS[drive.status_level];
            return (
              <div key={i} className="bg-gray-700/50 p-3 rounded-md">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-bold text-white">{drive.name}</span>
                  <span className={`text-xs font-semibold ${colors.badge}`}>
                    {drive.status_label}
                  </span>
                </div>
                <div className="w-full bg-gray-600 rounded-full h-2.5 my-2">
                  <div
                    className={`h-2.5 rounded-full ${colors.bar}`}
                    style={{ width: `${100 - drive.free_percent}%` }}
                  />
                </div>
                <div className="text-xs text-gray-400 flex justify-between">
                  <span>
                    {drive.free_gb} GB free ({drive.free_percent}%)
                  </span>
                  <span>{drive.total_gb} GB total</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
