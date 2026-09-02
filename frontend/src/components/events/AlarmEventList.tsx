import React from 'react';
import type { AlarmEvent } from '../../lib/eventsApi';
import {
  formatOccurredAt,
  severityBadgeClass,
  sourceTypeLabel,
  statusBadgeClass,
} from '../../lib/eventLabels';

interface AlarmEventListProps {
  events: AlarmEvent[];
  total: number;
  offset: number;
  limit: number;
  loading?: boolean;
  cameraLabel: (cameraId: string, cameraUid: string) => string;
  onSelect: (event: AlarmEvent) => void;
  onPrev: () => void;
  onNext: () => void;
}

export default function AlarmEventList({
  events,
  total,
  offset,
  limit,
  loading = false,
  cameraLabel,
  onSelect,
  onPrev,
  onNext,
}: AlarmEventListProps): React.ReactElement {
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex flex-wrap items-center justify-between gap-2 text-sm text-gray-500 dark:text-gray-400">
        <span>
          <strong className="text-gray-900 dark:text-white">{total}</strong> event{total === 1 ? '' : 's'}
          {loading ? ' · refreshing…' : ''}
        </span>
        <span>
          Page {page} of {totalPages}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left text-gray-600 dark:text-gray-300">
          <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase text-gray-700 dark:text-gray-400">
            <tr>
              <th className="px-4 py-3">Date / Time</th>
              <th className="px-4 py-3">Camera</th>
              <th className="px-4 py-3">Event</th>
              <th className="px-4 py-3">Source</th>
              <th className="px-4 py-3">Severity</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Acknowledged</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev) => (
              <tr
                key={ev.id}
                onClick={() => onSelect(ev)}
                className="border-b border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/20 cursor-pointer"
              >
                <td className="px-4 py-3 whitespace-nowrap text-xs">{formatOccurredAt(ev.occurred_at)}</td>
                <td className="px-4 py-3">{cameraLabel(ev.camera_id, ev.camera_uid)}</td>
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-900 dark:text-white">{ev.title}</div>
                  <div className="text-xs text-gray-500 truncate max-w-xs">{ev.message}</div>
                </td>
                <td className="px-4 py-3">{sourceTypeLabel(ev.source_type)}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${severityBadgeClass(ev.severity)}`}>
                    {ev.severity}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${statusBadgeClass(ev.status)}`}>
                    {ev.status}
                  </span>
                </td>
                <td className="px-4 py-3">{ev.acknowledged ? 'Yes' : 'No'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {events.length === 0 && !loading && (
        <p className="text-center text-gray-500 dark:text-gray-400 py-10">No events found</p>
      )}

      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-between">
        <button
          type="button"
          onClick={onPrev}
          disabled={!hasPrev || loading}
          className="btn-secondary px-3 py-1.5 text-sm w-auto disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={!hasNext || loading}
          className="btn-secondary px-3 py-1.5 text-sm w-auto disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
