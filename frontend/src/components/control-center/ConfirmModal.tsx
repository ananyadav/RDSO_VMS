import React from 'react';

interface ConfirmModalProps {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function ConfirmModal({
  open,
  title,
  body,
  confirmLabel,
  danger = false,
  busy = false,
  onCancel,
  onConfirm,
}: ConfirmModalProps): React.ReactElement | null {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl">
        <div className="p-5">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h3>
          <div className="mt-3 text-sm text-gray-600 dark:text-gray-300 space-y-2">{body}</div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200 dark:border-gray-700">
          <button type="button" onClick={onCancel} disabled={busy} className="btn-secondary px-4 py-2 text-sm w-auto">
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`px-4 py-2 text-sm w-auto rounded-md font-semibold text-white disabled:opacity-50 ${
              danger ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'
            }`}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
