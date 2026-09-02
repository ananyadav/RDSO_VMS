import React from 'react';
import { SlidersHorizontal } from 'lucide-react';
import type { EventListFilters } from '../../lib/eventQuery';
import {
  ACK_FILTER_OPTIONS,
  EVENT_SEVERITY_OPTIONS,
  EVENT_SOURCE_OPTIONS,
  EVENT_STATUS_OPTIONS,
} from '../../lib/eventLabels';

export interface CameraFilterOption {
  id: string;
  label: string;
}

interface AlarmEventFilterProps {
  filters: EventListFilters;
  cameras: CameraFilterOption[];
  camerasLoading?: boolean;
  onChange: (patch: Partial<EventListFilters>) => void;
  onApply: () => void;
}

export default function AlarmEventFilter({
  filters,
  cameras,
  camerasLoading = false,
  onChange,
  onApply,
}: AlarmEventFilterProps): React.ReactElement {
  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 sticky top-4">
      <div className="flex items-center pb-4 border-b border-gray-200 dark:border-gray-700">
        <SlidersHorizontal size={18} className="mr-3 text-gray-400" />
        <h3 className="text-lg font-bold text-gray-900 dark:text-white">Filters</h3>
      </div>

      <div className="mt-4 space-y-4 text-sm">
        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Camera</label>
          <select
            value={filters.camera_id}
            onChange={(e) => onChange({ camera_id: e.target.value, offset: 0 })}
            className="input-field w-full"
            disabled={camerasLoading}
          >
            <option value="">{camerasLoading ? 'Loading…' : 'All cameras'}</option>
            {cameras.map((cam) => (
              <option key={cam.id} value={cam.id}>
                {cam.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Source Type</label>
          <select
            value={filters.source_type}
            onChange={(e) => onChange({ source_type: e.target.value, offset: 0 })}
            className="input-field w-full"
          >
            {EVENT_SOURCE_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value} disabled={'available' in opt && !opt.available}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Severity</label>
          <select
            value={filters.severity}
            onChange={(e) => onChange({ severity: e.target.value, offset: 0 })}
            className="input-field w-full"
          >
            {EVENT_SEVERITY_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Status</label>
          <select
            value={filters.status}
            onChange={(e) => onChange({ status: e.target.value, offset: 0 })}
            className="input-field w-full"
          >
            {EVENT_STATUS_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Acknowledged</label>
          <select
            value={filters.acknowledged}
            onChange={(e) =>
              onChange({
                acknowledged: e.target.value as EventListFilters['acknowledged'],
                offset: 0,
              })
            }
            className="input-field w-full"
          >
            {ACK_FILTER_OPTIONS.map((opt) => (
              <option key={opt.value || 'all'} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Date From</label>
          <input
            type="date"
            value={filters.from}
            onChange={(e) => onChange({ from: e.target.value, offset: 0 })}
            className="input-field w-full"
          />
        </div>

        <div>
          <label className="block font-medium text-gray-700 dark:text-gray-300 mb-1">Date To</label>
          <input
            type="date"
            value={filters.to}
            onChange={(e) => onChange({ to: e.target.value, offset: 0 })}
            className="input-field w-full"
          />
        </div>

        <button type="button" onClick={onApply} className="btn-primary w-full py-2 text-sm">
          Apply Filters
        </button>
      </div>
    </div>
  );
}
