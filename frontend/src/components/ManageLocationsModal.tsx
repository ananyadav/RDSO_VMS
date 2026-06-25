import React from 'react';
import { X, MapPin } from 'lucide-react';
import LocationMasterPanel from './location-master/LocationMasterPanel';
import type { LocationSite } from '../constants/corporateFloors';

interface ManageLocationsModalProps {
  isOpen: boolean;
  onClose: () => void;
  sites: LocationSite[];
  onUpdated: () => void;
  /** Render above Add/Edit Camera modal when opened from camera form */
  stacked?: boolean;
}

export default function ManageLocationsModal({
  isOpen,
  onClose,
  sites,
  onUpdated,
  stacked = false,
}: ManageLocationsModalProps) {
  if (!isOpen) return null;

  return (
    <div className={`fixed inset-0 flex items-center justify-center bg-black/60 p-4 ${stacked ? 'z-[60]' : 'z-50'}`}>
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700 sticky top-0 bg-white dark:bg-gray-900 z-10">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <MapPin size={20} className="text-emerald-400" />
            Manage Locations
          </h3>
          <button type="button" onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <X size={20} />
          </button>
        </div>
        <div className="p-5">
          <LocationMasterPanel sites={sites} onUpdated={onUpdated} />
        </div>
      </div>
    </div>
  );
}
