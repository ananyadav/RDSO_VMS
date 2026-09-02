import React from 'react';
import { X } from 'lucide-react';
import type { AlarmEvent } from '../../lib/eventsApi';
import { actionLabel } from '../../lib/alarmRuleLabels';
import {
  formatOccurredAt,
  recordingStatusLabel,
  safeMetadataEntries,
  severityBadgeClass,
  sourceTypeLabel,
  statusBadgeClass,
} from '../../lib/eventLabels';

interface AlarmEventDetailModalProps {
  event: AlarmEvent | null;
  cameraLabel: string;
  acknowledging?: boolean;
  onClose: () => void;
  onAcknowledge: (event: AlarmEvent) => void;
}

export default function AlarmEventDetailModal({
  event,
  cameraLabel,
  acknowledging = false,
  onClose,
  onAcknowledge,
}: AlarmEventDetailModalProps): React.ReactElement | null {
  if (!event) return null;

  const meta = safeMetadataEntries(event.metadata);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="w-full max-w-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">{event.title}</h2>
          <button type="button" onClick={onClose} className="p-1 text-gray-500 hover:text-gray-900 dark:hover:text-white rounded" aria-label="Close">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3 text-sm">
          <DetailRow label="Occurred">{formatOccurredAt(event.occurred_at)}</DetailRow>
          <DetailRow label="Camera">{cameraLabel}</DetailRow>
          <DetailRow label="Source">{sourceTypeLabel(event.source_type)}</DetailRow>
          <DetailRow label="Severity">
            <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${severityBadgeClass(event.severity)}`}>
              {event.severity}
            </span>
          </DetailRow>
          <DetailRow label="Status">
            <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${statusBadgeClass(event.status)}`}>
              {event.status}
            </span>
          </DetailRow>
          <DetailRow label="Message">{event.message}</DetailRow>
          {event.actions_triggered.length > 0 && (
            <DetailRow label="Actions">
              {event.actions_triggered.map(actionLabel).join(', ')}
            </DetailRow>
          )}
          {event.actions_triggered.includes('start_recording') && (
            <>
              {event.recording_session_id && (
                <DetailRow label="Recording Session">{event.recording_session_id}</DetailRow>
              )}
              {event.recording_status && (
                <DetailRow label="Recording Status">
                  {recordingStatusLabel(event.recording_status)}
                </DetailRow>
              )}
            </>
          )}
          {event.acknowledged && (
            <>
              <DetailRow label="Acknowledged">Yes</DetailRow>
              {event.acknowledged_at && (
                <DetailRow label="Acknowledged at">{formatOccurredAt(event.acknowledged_at)}</DetailRow>
              )}
            </>
          )}
          {meta.length > 0 && (
            <div>
              <div className="text-gray-500 dark:text-gray-400 mb-1">Details</div>
              <dl className="bg-gray-50 dark:bg-gray-900/50 rounded-md p-3 space-y-1">
                {meta.map(([k, v]) => (
                  <div key={k} className="flex gap-2">
                    <dt className="text-gray-500 shrink-0">{k}:</dt>
                    <dd className="text-gray-800 dark:text-gray-200 break-all">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-4 border-t border-gray-200 dark:border-gray-700">
          <button type="button" onClick={onClose} className="btn-secondary px-4 py-2 text-sm w-auto">
            Close
          </button>
          {!event.acknowledged && (
            <button
              type="button"
              disabled={acknowledging}
              onClick={() => onAcknowledge(event)}
              className="btn-primary px-4 py-2 text-sm w-auto disabled:opacity-50"
            >
              {acknowledging ? 'Acknowledging…' : 'Acknowledge'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-gray-500 dark:text-gray-400">{label}</div>
      <div className="text-gray-900 dark:text-gray-100">{children}</div>
    </div>
  );
}
