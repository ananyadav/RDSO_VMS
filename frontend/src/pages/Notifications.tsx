import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { AlertTriangle, Bell, CheckCircle, Info } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { apiFetch, cameraQuery, readJsonResponse } from '../lib/api';
import { mergeEventPage } from '../lib/eventQuery';
import {
  EventsRequestError,
  acknowledgeEvent,
  listUiNotifications,
  type AlarmEvent,
} from '../lib/eventsApi';
import {
  formatOccurredAt,
  severityBadgeClass,
  sourceTypeLabel,
  statusBadgeClass,
} from '../lib/eventLabels';
import { useVisibilityInterval } from '../hooks/useVisibilityInterval';

const POLL_INTERVAL_MS = 8000;

interface ConfiguredCamera {
  _id: string;
  name?: string;
  display_name?: string;
  ip_address?: string;
  camera_uid?: string;
}

type StatusFilter = 'all' | 'active' | 'acknowledged';
type SeverityFilter = 'all' | 'info' | 'warning' | 'critical';

function severityIcon(severity: string) {
  switch (severity) {
    case 'critical':
      return AlertTriangle;
    case 'warning':
      return Bell;
    default:
      return Info;
  }
}

function cameraDisplay(cam: ConfiguredCamera): string {
  return (
    (cam.display_name || cam.name || cam.camera_uid || '').trim() ||
    cam.ip_address ||
    cam._id
  );
}

export default function Notifications(): React.ReactElement {
  const [notifications, setNotifications] = useState<AlarmEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all');
  const [acknowledgingId, setAcknowledgingId] = useState<string | null>(null);
  const [cameras, setCameras] = useState<ConfiguredCamera[]>([]);

  const inFlightRef = useRef(false);

  const cameraById = useMemo(() => {
    const map = new Map<string, ConfiguredCamera>();
    for (const cam of cameras) {
      map.set(cam._id, cam);
    }
    return map;
  }, [cameras]);

  const resolveCameraLabel = useCallback(
    (event: AlarmEvent) => {
      const cam = cameraById.get(event.camera_id);
      if (cam) {
        const label = cameraDisplay(cam);
        return cam.ip_address ? `${label} (${cam.ip_address})` : label;
      }
      return event.camera_uid || event.camera_id;
    },
    [cameraById],
  );

  const fetchNotifications = useCallback(async (opts?: { silent?: boolean }) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!opts?.silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      const data = await listUiNotifications({
        acknowledged:
          statusFilter === 'active' ? false : statusFilter === 'acknowledged' ? true : undefined,
        severity: severityFilter === 'all' ? undefined : severityFilter,
        limit: 50,
        offset: 0,
      });
      setNotifications((prev) =>
        opts?.silent ? mergeEventPage(prev, data.items) : data.items,
      );
      setLoadError(null);
    } catch (err) {
      if (!opts?.silent) {
        const message =
          err instanceof EventsRequestError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Failed to load notifications';
        setLoadError(message);
      }
    } finally {
      inFlightRef.current = false;
      if (!opts?.silent) setLoading(false);
    }
  }, [severityFilter, statusFilter]);

  useEffect(() => {
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/cameras/configured${cameraQuery({ includeInactive: 'true' })}`,
        );
        const data = await readJsonResponse<ConfiguredCamera[] | { items?: ConfiguredCamera[] }>(
          response,
        );
        setCameras(Array.isArray(data) ? data : data.items ?? []);
      } catch {
        /* camera labels optional */
      }
    })();
  }, []);

  useEffect(() => {
    void fetchNotifications();
  }, [fetchNotifications]);

  useVisibilityInterval(() => {
    void fetchNotifications({ silent: true });
  }, POLL_INTERVAL_MS);

  const handleAcknowledge = async (event: AlarmEvent) => {
    setAcknowledgingId(event.id);
    try {
      const updated = await acknowledgeEvent(event.id);
      toast.success('Notification acknowledged');
      setNotifications((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
    } catch (err) {
      toast.error(
        err instanceof EventsRequestError ? err.message : 'Failed to acknowledge notification',
      );
    } finally {
      setAcknowledgingId(null);
    }
  };

  const showInitialLoading = loading && notifications.length === 0 && !loadError;

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Alerts"
        subtitle="Alarm notifications from rules with Show UI Notification enabled"
      />

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <div className="flex flex-wrap gap-2">
          {(['all', 'active', 'acknowledged'] as StatusFilter[]).map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setStatusFilter(opt)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md capitalize ${
                statusFilter === opt
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
              }`}
            >
              {opt === 'active' ? 'Unacknowledged' : opt}
            </button>
          ))}
          <span className="w-px bg-gray-300 dark:bg-gray-700 mx-1" />
          {(['all', 'info', 'warning', 'critical'] as SeverityFilter[]).map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => setSeverityFilter(opt)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md capitalize ${
                severityFilter === opt
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-200 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
              }`}
            >
              {opt}
            </button>
          ))}
        </div>

        {showInitialLoading && (
          <div className="text-center py-12 text-gray-500 dark:text-gray-400">Loading notifications…</div>
        )}

        {!showInitialLoading && loadError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-center">
            <p className="text-red-300 mb-3">{loadError}</p>
            <button
              type="button"
              onClick={() => void fetchNotifications()}
              className="btn-secondary px-4 py-2 text-sm w-auto"
            >
              Retry
            </button>
          </div>
        )}

        {!showInitialLoading && !loadError && notifications.length === 0 && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-10 text-center">
            <p className="text-gray-600 dark:text-gray-300">No notifications found</p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
              Alarm rules with Show UI Notification will appear here when triggered.
            </p>
          </div>
        )}

        {!showInitialLoading && !loadError && notifications.length > 0 && (
          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-200 dark:divide-gray-700">
            {notifications.map((n) => {
              const Icon = severityIcon(n.severity);
              return (
                <div
                  key={n.id}
                  className="p-4 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3"
                >
                  <div className="flex gap-3 min-w-0">
                    <Icon className={`w-5 h-5 shrink-0 mt-0.5 ${severityBadgeClass(n.severity).split(' ')[1] || 'text-gray-400'}`} />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <h3 className="font-semibold text-gray-900 dark:text-white">{n.title}</h3>
                        <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${severityBadgeClass(n.severity)}`}>
                          {n.severity}
                        </span>
                        <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${statusBadgeClass(n.status)}`}>
                          {n.acknowledged ? 'Acknowledged' : 'Active'}
                        </span>
                      </div>
                      <p className="text-sm text-gray-600 dark:text-gray-300">{n.message}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        {resolveCameraLabel(n)} · {sourceTypeLabel(n.source_type)} ·{' '}
                        {formatOccurredAt(n.occurred_at)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 sm:ml-4">
                    <Link
                      to={`/events?event=${encodeURIComponent(n.id)}`}
                      className="btn-secondary px-3 py-1.5 text-xs w-auto"
                    >
                      View Event
                    </Link>
                    {!n.acknowledged && (
                      <button
                        type="button"
                        disabled={acknowledgingId === n.id}
                        onClick={() => void handleAcknowledge(n)}
                        className="btn-primary px-3 py-1.5 text-xs w-auto flex items-center gap-1 disabled:opacity-50"
                      >
                        <CheckCircle size={14} />
                        {acknowledgingId === n.id ? 'Acknowledging…' : 'Acknowledge'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
