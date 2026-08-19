import React from 'react';
import { X } from 'lucide-react';
import type { AuditLogItem } from '../../lib/controlCenterApi';
import {
  actorDisplayName,
  displayRole,
  formatChangeValue,
  formatLocalDateTime,
  humanizeField,
  loginFailureLabel,
} from '../../lib/superAdmin';

function ChangeRows({ changes }: { changes: Record<string, unknown> }) {
  const entries = Object.entries(changes || {});
  if (!entries.length) {
    return <p className="text-sm text-gray-500">No field changes recorded.</p>;
  }

  return (
    <div className="space-y-3">
      {entries.map(([field, raw]) => {
        if (field === 'password_changed') {
          return (
            <div key={field} className="text-sm">
              <p className="text-gray-500 dark:text-gray-400">{humanizeField(field)}</p>
              <p className="text-gray-900 dark:text-white">Changed</p>
            </div>
          );
        }
        const delta = raw && typeof raw === 'object' && 'before' in (raw as object)
          ? (raw as { before?: unknown; after?: unknown })
          : null;
        if (!delta) {
          return (
            <div key={field} className="text-sm">
              <p className="text-gray-500 dark:text-gray-400">{humanizeField(field)}</p>
              <p className="text-gray-900 dark:text-white break-all">{formatChangeValue(raw)}</p>
            </div>
          );
        }
        return (
          <div key={field} className="text-sm border border-gray-200 dark:border-gray-700 rounded-md p-3">
            <p className="font-medium text-gray-900 dark:text-white mb-2">{humanizeField(field)}</p>
            <div className="grid grid-cols-[1fr_auto_1fr] gap-2 items-start">
              <p className="text-gray-600 dark:text-gray-300 break-all">{formatChangeValue(delta.before)}</p>
              <p className="text-gray-400 pt-0.5">→</p>
              <p className="text-gray-900 dark:text-white break-all">{formatChangeValue(delta.after)}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 py-2 border-b border-gray-100 dark:border-gray-700/60">
      <dt className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">{label}</dt>
      <dd className="col-span-2 text-sm text-gray-900 dark:text-gray-100 break-all">{value || '—'}</dd>
    </div>
  );
}

export default function AuditDetailModal({
  item,
  onClose,
}: {
  item: AuditLogItem | null;
  onClose: () => void;
}): React.ReactElement | null {
  if (!item) return null;
  const failureLabel = loginFailureLabel(item.metadata);
  const resource = [item.resource_type, item.resource_label || item.resource_id].filter(Boolean).join(' · ');

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-stretch justify-end">
      <button type="button" className="flex-1 cursor-default" aria-label="Close details" onClick={onClose} />
      <aside className="w-full max-w-lg bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 h-full overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Audit detail</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-900 dark:hover:text-white">
            <X size={20} />
          </button>
        </div>
        <dl className="p-4">
          <DetailRow label="Actor" value={actorDisplayName(item)} />
          <DetailRow label="Role" value={displayRole(item.actor_role)} />
          <DetailRow label="Timestamp" value={formatLocalDateTime(item.timestamp)} />
          <DetailRow label="IP" value={item.ip_address || '—'} />
          <DetailRow label="User Agent" value={item.user_agent || '—'} />
          <DetailRow label="Action" value={item.action || '—'} />
          <DetailRow label="Resource type" value={item.resource_type || '—'} />
          <DetailRow label="Resource ID" value={item.resource_id || '—'} />
          <DetailRow label="Resource" value={resource || '—'} />
          <DetailRow
            label="Result"
            value={item.success === false ? 'Failure' : 'Success'}
          />
          {failureLabel ? <DetailRow label="Details" value={failureLabel} /> : null}
        </dl>
        <div className="px-4 pb-6">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">Before → After</h4>
          <ChangeRows changes={(item.changes as Record<string, unknown>) || {}} />
        </div>
      </aside>
    </div>
  );
}
