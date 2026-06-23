import React from 'react';
import { AlertTriangle, X } from 'lucide-react';

export interface ExistingCameraInfo {
  id: string;
  name: string;
  ip_address: string;
  building?: string;
  floor?: string;
  floor_group?: string;
  camera_group?: string;
  location_path?: string;
  is_active: boolean;
}

interface DuplicateCameraDialogProps {
  isOpen: boolean;
  message: string;
  existing: ExistingCameraInfo;
  pendingName?: string;
  onClose: () => void;
  onView: () => void;
  onEdit: () => void;
  onReactivate?: () => void;
  onReplace?: () => void;
  replaceLoading?: boolean;
}

export default function DuplicateCameraDialog({
  isOpen,
  message,
  existing,
  pendingName,
  onClose,
  onView,
  onEdit,
  onReactivate,
  onReplace,
  replaceLoading = false,
}: DuplicateCameraDialogProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[60] p-4">
      <div className="bg-gray-800 border border-amber-600/50 rounded-lg shadow-xl w-full max-w-md">
        <div className="flex items-start justify-between p-4 border-b border-gray-700">
          <div className="flex items-center gap-2 text-amber-400">
            <AlertTriangle size={22} />
            <h3 className="text-lg font-bold text-white">Duplicate Camera</h3>
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-white">
            <X size={22} />
          </button>
        </div>
        <div className="p-4 space-y-3 text-sm text-gray-300">
          <p>{message}</p>
          <div className="bg-gray-900/60 rounded-md p-3 text-xs space-y-1 font-mono">
            <div><span className="text-gray-500">Name:</span> {existing.name}</div>
            <div><span className="text-gray-500">IP:</span> {existing.ip_address}</div>
            <div><span className="text-gray-500">Location:</span> {existing.location_path || `${existing.building} / ${existing.floor}`}</div>
            <div><span className="text-gray-500">Status:</span> {existing.is_active ? 'Active' : 'Disabled'}</div>
          </div>
        </div>
        <div className="flex flex-col gap-2 p-4 border-t border-gray-700">
          {pendingName && onReplace && (
            <button
              type="button"
              onClick={onReplace}
              disabled={replaceLoading}
              className="w-full text-sm px-3 py-2.5 rounded-md font-medium bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-60"
            >
              {replaceLoading
                ? 'Replacing…'
                : `Delete "${existing.name}" & add "${pendingName}"`}
            </button>
          )}
          <div className="flex flex-wrap gap-2 justify-end">
            <button type="button" onClick={onClose} className="btn-secondary text-sm px-3 py-2">Cancel</button>
            <button type="button" onClick={onView} className="btn-secondary text-sm px-3 py-2">View in Table</button>
            <button type="button" onClick={onEdit} className="btn-primary text-sm px-3 py-2">Edit Existing</button>
            {!existing.is_active && onReactivate && (
              <button type="button" onClick={onReactivate} className="btn-primary text-sm px-3 py-2 bg-emerald-600 hover:bg-emerald-500">
                Reactivate
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
