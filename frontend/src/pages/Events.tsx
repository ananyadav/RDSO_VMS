import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import PageHeader from '../components/PageHeader';
import AlarmEventFilter, { type CameraFilterOption } from '../components/events/AlarmEventFilter';
import AlarmEventList from '../components/events/AlarmEventList';
import AlarmEventDetailModal from '../components/events/AlarmEventDetailModal';
import { apiFetch, cameraQuery, readJsonResponse } from '../lib/api';
import {
  EVENT_POLL_INTERVAL_MS,
  buildEventQueryParams,
  defaultEventFilters,
  filtersKey,
  mergeEventPage,
  type EventListFilters,
} from '../lib/eventQuery';
import {
  EventsRequestError,
  acknowledgeEvent,
  getEvent,
  listEvents,
  type AlarmEvent,
} from '../lib/eventsApi';
import { useVisibilityInterval } from '../hooks/useVisibilityInterval';
import {
  useUrlHydration,
  useUrlSync,
  initialStringParam,
} from '../hooks/useUrlSearchState';

interface ConfiguredCamera {
  _id: string;
  name?: string;
  display_name?: string;
  ip_address?: string;
  camera_uid?: string;
}

function cameraDisplay(cam: ConfiguredCamera): string {
  return (
    (cam.display_name || cam.name || cam.camera_uid || '').trim() ||
    cam.ip_address ||
    cam._id
  );
}

function filtersFromParams(params: URLSearchParams): EventListFilters {
  const base = defaultEventFilters();
  const off = parseInt(params.get('offset') || '0', 10);
  return {
    ...base,
    camera_id: params.get('camera_id') || '',
    source_type: params.get('source_type') || '',
    severity: params.get('severity') || '',
    status: params.get('status') || '',
    acknowledged: (params.get('acknowledged') as EventListFilters['acknowledged']) || '',
    from: params.get('from') || '',
    to: params.get('to') || '',
    offset: Number.isFinite(off) ? Math.max(0, off) : 0,
  };
}

export default function Events(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();

  const [filters, setFilters] = useState<EventListFilters>(() =>
    filtersFromParams(initialParams.current ?? new URLSearchParams()),
  );
  const [events, setEvents] = useState<AlarmEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cameras, setCameras] = useState<ConfiguredCamera[]>([]);
  const [camerasLoading, setCamerasLoading] = useState(true);
  const [selectedEvent, setSelectedEvent] = useState<AlarmEvent | null>(() => {
    const id = initialStringParam(initialParams, 'event');
    return id ? ({ id } as AlarmEvent) : null;
  });
  const [acknowledging, setAcknowledging] = useState(false);

  const inFlightRef = useRef(false);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const activeFilterKeyRef = useRef(filtersKey(filters));

  const cameraById = useMemo(() => {
    const map = new Map<string, ConfiguredCamera>();
    for (const cam of cameras) {
      map.set(cam._id, cam);
    }
    return map;
  }, [cameras]);

  const cameraOptions: CameraFilterOption[] = useMemo(
    () =>
      cameras.map((cam) => ({
        id: cam._id,
        label: cam.ip_address ? `${cameraDisplay(cam)} (${cam.ip_address})` : cameraDisplay(cam),
      })),
    [cameras],
  );

  const resolveCameraLabel = useCallback(
    (cameraId: string, cameraUid: string) => {
      const cam = cameraById.get(cameraId);
      if (cam) {
        return cam.ip_address ? `${cameraDisplay(cam)} (${cam.ip_address})` : cameraDisplay(cam);
      }
      return cameraUid || cameraId;
    },
    [cameraById],
  );

  const fetchEvents = useCallback(async (opts?: { silent?: boolean; merge?: boolean }) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    const currentFilters = filtersRef.current;
    const requestKey = filtersKey(currentFilters);
    if (!opts?.silent) {
      setLoading(true);
      setLoadError(null);
    }
    try {
      const data = await listEvents(buildEventQueryParams(currentFilters));
      if (filtersKey(filtersRef.current) !== requestKey) return;
      setTotal(data.total);
      setEvents((prev) => {
        if (opts?.merge && currentFilters.offset === 0) {
          return mergeEventPage(prev, data.items);
        }
        return data.items;
      });
      setLoadError(null);
    } catch (err) {
      if (!opts?.silent) {
        const message =
          err instanceof EventsRequestError
            ? err.message
            : err instanceof Error
              ? err.message
              : 'Failed to load events';
        setLoadError(message);
      }
    } finally {
      inFlightRef.current = false;
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      setCamerasLoading(true);
      try {
        const response = await apiFetch(
          `/api/cameras/configured${cameraQuery({ includeInactive: 'true' })}`,
        );
        const data = await readJsonResponse<ConfiguredCamera[] | { items?: ConfiguredCamera[] }>(
          response,
        );
        setCameras(Array.isArray(data) ? data : data.items ?? []);
      } catch {
        toast.error('Failed to load cameras for filters');
      } finally {
        setCamerasLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    activeFilterKeyRef.current = filtersKey(filters);
    void fetchEvents();
  }, [filters, fetchEvents]);

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  useVisibilityInterval(
    () => {
      void fetchEvents({ silent: true, merge: true });
    },
    EVENT_POLL_INTERVAL_MS,
    true,
  );

  useEffect(() => {
    const id = selectedEvent?.id;
    if (!id || selectedEvent.title) return;
    void (async () => {
      try {
        const ev = await getEvent(id);
        setSelectedEvent(ev);
        setEvents((prev) => prev.map((e) => (e.id === ev.id ? ev : e)));
      } catch {
        setSelectedEvent(null);
      }
    })();
  }, [selectedEvent?.id, selectedEvent?.title]);

  const detailEvent = useMemo(() => {
    if (!selectedEvent) return null;
    if (selectedEvent.title) return selectedEvent;
    return events.find((e) => e.id === selectedEvent.id) ?? null;
  }, [selectedEvent, events]);

  const urlValues = useMemo(
    () => ({
      camera_id: filters.camera_id || null,
      source_type: filters.source_type || null,
      severity: filters.severity || null,
      status: filters.status || null,
      acknowledged: filters.acknowledged || null,
      from: filters.from || null,
      to: filters.to || null,
      offset: filters.offset > 0 ? String(filters.offset) : null,
      event: selectedEvent?.id ?? null,
    }),
    [filters, selectedEvent],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  const patchFilters = (patch: Partial<EventListFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
  };

  const handleAcknowledge = async (event: AlarmEvent) => {
    setAcknowledging(true);
    try {
      const updated = await acknowledgeEvent(event.id);
      toast.success('Event acknowledged');
      setEvents((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
      setSelectedEvent(updated);
    } catch (err) {
      toast.error(err instanceof EventsRequestError ? err.message : 'Failed to acknowledge event');
    } finally {
      setAcknowledging(false);
    }
  };

  const showInitialLoading = loading && events.length === 0 && !loadError;

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="Events"
        subtitle="Review and acknowledge alarm events from configured rules"
      />

      <div className="flex-1 overflow-y-auto p-4">
        {showInitialLoading && (
          <div className="text-center py-12 text-gray-500 dark:text-gray-400">Loading events…</div>
        )}

        {!showInitialLoading && loadError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-6 text-center mb-4">
            <p className="text-red-300 mb-3">{loadError}</p>
            <button type="button" onClick={() => void fetchEvents()} className="btn-secondary px-4 py-2 text-sm w-auto">
              Retry
            </button>
          </div>
        )}

        {!showInitialLoading && (
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            <div className="lg:col-span-1">
              <AlarmEventFilter
                filters={filters}
                cameras={cameraOptions}
                camerasLoading={camerasLoading}
                onChange={patchFilters}
                onApply={() => void fetchEvents()}
              />
            </div>
            <div className="lg:col-span-3">
              <AlarmEventList
                events={events}
                total={total}
                offset={filters.offset}
                limit={filters.limit}
                loading={loading}
                cameraLabel={resolveCameraLabel}
                onSelect={setSelectedEvent}
                onPrev={() => patchFilters({ offset: Math.max(0, filters.offset - filters.limit) })}
                onNext={() => patchFilters({ offset: filters.offset + filters.limit })}
              />
            </div>
          </div>
        )}
      </div>

      <AlarmEventDetailModal
        event={detailEvent}
        cameraLabel={
          detailEvent
            ? resolveCameraLabel(detailEvent.camera_id, detailEvent.camera_uid)
            : ''
        }
        acknowledging={acknowledging}
        onClose={() => setSelectedEvent(null)}
        onAcknowledge={handleAcknowledge}
      />
    </div>
  );
}
