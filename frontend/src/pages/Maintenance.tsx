import React from "react";
import PageHeader from "../components/PageHeader";
import Card from "../components/Card";
import toast from "react-hot-toast";

export default function Maintenance(): React.ReactElement {
  const handleBackup = async () => {
    try {
      const res = await fetch('/api/maintenance/backup', { method: 'POST' });
      if (res.ok) toast.success('Backup started successfully');
      else toast.error('Backup request failed');
    } catch {
      toast.error('Backup not available — backend endpoint not implemented yet');
    }
  };

  const handleExportLogs = async () => {
    try {
      const res = await fetch('/api/maintenance/logs');
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'system_logs.txt';
        a.click();
        URL.revokeObjectURL(url);
      } else {
        toast.error('Log export request failed');
      }
    } catch {
      toast.error('Log export not available — backend endpoint not implemented yet');
    }
  };

  const handleFirmwareUpdate = async () => {
    try {
      const res = await fetch('/api/maintenance/firmware-update', { method: 'POST' });
      if (res.ok) toast.success('Firmware update initiated');
      else toast.error('Firmware update request failed');
    } catch {
      toast.error('Firmware update not available — backend endpoint not implemented yet');
    }
  };

  return (
    <div>
      <PageHeader title="Maintenance" subtitle="Backups, logs, firmware" />
      <Card>
        <div className="flex flex-wrap gap-3">
          <button
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-md transition-colors"
            onClick={handleBackup}
          >
            Backup
          </button>
          <button
            className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white font-semibold rounded-md transition-colors"
            onClick={handleExportLogs}
          >
            Export Logs
          </button>
          <button
            className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white font-semibold rounded-md transition-colors"
            onClick={handleFirmwareUpdate}
          >
            Update Firmware
          </button>
        </div>
      </Card>
    </div>
  );
}
