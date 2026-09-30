import React, { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Download, Loader2 } from 'lucide-react';
import PageHeader from '../components/PageHeader';
import { apiFetch, cameraQuery, readJsonResponse } from '../lib/api';
import {
  EVENT_SEVERITY_OPTIONS,
  EVENT_SOURCE_OPTIONS,
  EVENT_STATUS_OPTIONS,
  formatOccurredAt,
  sourceTypeLabel,
} from '../lib/eventLabels';
import { localDayEndIso, localDayStartIso } from '../lib/superAdmin';
import {
  downloadReportCsv,
  fetchAlarmReport,
  fetchIncidentReport,
  fetchOperatorLogReport,
  ReportsRequestError,
  type AlarmReportRow,
  type IncidentReportRow,
  type OperatorLogReportRow,
} from '../lib/reportsApi';
import { AUDIT_ACTIONS } from '../components/control-center/AuditLogTable';
import { authService } from '../services/authService';
import { hasPermission, isOpsAdminUser, PERMISSIONS } from '../lib/permissions';
import { useUrlHydration, useUrlSync } from '../hooks/useUrlSearchState';

type ReportTab = 'alarms' | 'incidents' | 'operator-logs';
const PAGE_SIZE = 50;

type CameraOpt = { id: string; label: string };

function tabFromParams(params: URLSearchParams): ReportTab {
  const t = (params.get('tab') || 'alarms').toLowerCase();
  if (t === 'incidents' || t === 'incident') return 'incidents';
  if (t === 'operator-logs' || t === 'operator' || t === 'logs') return 'operator-logs';
  return 'alarms';
}

export default function Reports(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const user = authService.getCurrentUser();
  const canEvents = hasPermission(user, PERMISSIONS.EVENTS);
  const canOperatorLogs = isOpsAdminUser(user);

  const [tab, setTab] = useState<ReportTab>(() => tabFromParams(initialParams.current ?? new URLSearchParams()));
  const [cameras, setCameras] = useState<CameraOpt[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);

  const [cameraId, setCameraId] = useState('');
  const [sourceType, setSourceType] = useState('');
  const [severity, setSeverity] = useState('');
  const [status, setStatus] = useState('');
  const [acknowledged, setAcknowledged] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [actorUser, setActorUser] = useState('');
  const [action, setAction] = useState('');
  const [resourceId, setResourceId] = useState('');

  const [alarmRows, setAlarmRows] = useState<AlarmReportRow[]>([]);
  const [incidentRows, setIncidentRows] = useState<IncidentReportRow[]>([]);
  const [operatorRows, setOperatorRows] = useState<OperatorLogReportRow[]>([]);

  useUrlSync(hydratedRef, setParams, { tab });
  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await apiFetch(
          `/api/cameras/configured${cameraQuery({ includeInactive: 'true' })}`,
        );
        const data = await readJsonResponse<
          Array<Record<string, string>> | { items?: Array<Record<string, string>>; cameras?: Array<Record<string, string>> }
        >(response);
        if (!response.ok || cancelled) return;
        const list = Array.isArray(data)
          ? data
          : data.items || data.cameras || [];
        const opts = list
          .map((c) => ({
            id: String(c._id || c.id || ''),
            label: String(c.display_name || c.name || c.camera_uid || c.ip_address || c._id || ''),
          }))
          .filter((c) => c.id);
        setCameras(opts);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (tab === 'operator-logs' && !canOperatorLogs && canEvents) {
      setTab('alarms');
    }
  }, [tab, canOperatorLogs, canEvents]);

  const dateParams = useMemo(
    () => ({
      from: from ? localDayStartIso(from) : undefined,
      to: to ? localDayEndIso(to) : undefined,
    }),
    [from, to],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (tab === 'alarms') {
        if (!canEvents) throw new ReportsRequestError('Events permission required', 403);
        const data = await fetchAlarmReport({
          camera_id: cameraId || undefined,
          source_type: sourceType || undefined,
          severity: severity || undefined,
          status: status || undefined,
          acknowledged: acknowledged || undefined,
          ...dateParams,
          limit: PAGE_SIZE,
          offset,
        });
        setAlarmRows(data.items || []);
        setTotal(data.total || 0);
        setIncidentRows([]);
        setOperatorRows([]);
      } else if (tab === 'incidents') {
        if (!canEvents) throw new ReportsRequestError('Events permission required', 403);
        const data = await fetchIncidentReport({
          camera_id: cameraId || undefined,
          status: status || undefined,
          severity: severity || undefined,
          ...dateParams,
          limit: PAGE_SIZE,
          offset,
        });
        setIncidentRows(data.items || []);
        setTotal(data.total || 0);
        setAlarmRows([]);
        setOperatorRows([]);
      } else {
        if (!canOperatorLogs) throw new ReportsRequestError('Admin only', 403);
        const data = await fetchOperatorLogReport({
          user: actorUser || undefined,
          action: action || undefined,
          camera_id: cameraId || undefined,
          resource_id: resourceId || undefined,
          ...dateParams,
          limit: PAGE_SIZE,
          offset,
        });
        setOperatorRows(data.items || []);
        setTotal(data.total || 0);
        setAlarmRows([]);
        setIncidentRows([]);
      }
    } catch (err) {
      const message =
        err instanceof ReportsRequestError ? err.message : 'Could not load report.';
      setError(message);
      setAlarmRows([]);
      setIncidentRows([]);
      setOperatorRows([]);
      setTotal(0);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [
    acknowledged,
    action,
    actorUser,
    cameraId,
    canEvents,
    canOperatorLogs,
    dateParams,
    offset,
    resourceId,
    severity,
    sourceType,
    status,
    tab,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  const onExport = async () => {
    setExporting(true);
    try {
      const common = { ...dateParams, camera_id: cameraId || undefined };
      if (tab === 'alarms') {
        await downloadReportCsv(
          'alarms',
          {
            ...common,
            source_type: sourceType || undefined,
            severity: severity || undefined,
            status: status || undefined,
            acknowledged: acknowledged || undefined,
          },
          'alarm-report.csv',
        );
      } else if (tab === 'incidents') {
        await downloadReportCsv(
          'incidents',
          {
            ...common,
            status: status || undefined,
            severity: severity || undefined,
          },
          'incident-report.csv',
        );
      } else {
        await downloadReportCsv(
          'operator-logs',
          {
            ...common,
            user: actorUser || undefined,
            action: action || undefined,
            resource_id: resourceId || undefined,
          },
          'operator-logs.csv',
        );
      }
      toast.success('CSV downloaded');
    } catch (err) {
      toast.error(err instanceof ReportsRequestError ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const switchTab = (next: ReportTab) => {
    setTab(next);
    setOffset(0);
    setError(null);
  };

  const pageLabel = total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`;

  return (
    <div className="h-full flex flex-col p-3 sm:p-4 gap-3 min-h-0">
      <PageHeader
        title="Reports"
        subtitle="Alarm, incident, and operator activity from live Events and Audit data"
        rightContent={
          <button
            type="button"
            onClick={() => void onExport()}
            disabled={exporting || loading}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-gray-800 text-white text-sm hover:bg-gray-700 disabled:opacity-50"
          >
            {exporting ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            Export CSV
          </button>
        }
      />

      <div className="flex flex-wrap gap-2 border-b border-gray-300 dark:border-gray-700 pb-2">
        {canEvents && (
          <>
            <TabButton active={tab === 'alarms'} onClick={() => switchTab('alarms')}>
              Alarm Reports
            </TabButton>
            <TabButton active={tab === 'incidents'} onClick={() => switchTab('incidents')}>
              Incident Reports
            </TabButton>
          </>
        )}
        {canOperatorLogs && (
          <TabButton active={tab === 'operator-logs'} onClick={() => switchTab('operator-logs')}>
            Operator Logs
          </TabButton>
        )}
      </div>

      <div className="flex flex-wrap gap-2 items-end bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-3">
        <Field label="From">
          <input type="date" className="input-style py-1.5 px-2" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} />
        </Field>
        <Field label="To">
          <input type="date" className="input-style py-1.5 px-2" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} />
        </Field>
        {(tab === 'alarms' || tab === 'incidents' || tab === 'operator-logs') && (
          <Field label="Camera">
            <select className="select-style" value={cameraId} onChange={(e) => { setCameraId(e.target.value); setOffset(0); }}>
              <option value="">All cameras</option>
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>{c.label}</option>
              ))}
            </select>
          </Field>
        )}
        {tab === 'alarms' && (
          <>
            <Field label="Event type">
              <select className="select-style" value={sourceType} onChange={(e) => { setSourceType(e.target.value); setOffset(0); }}>
                {EVENT_SOURCE_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Severity">
              <select className="select-style" value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}>
                {EVENT_SEVERITY_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <select className="select-style" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
                {EVENT_STATUS_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Acknowledged">
              <select className="select-style" value={acknowledged} onChange={(e) => { setAcknowledged(e.target.value); setOffset(0); }}>
                <option value="">Any</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </Field>
          </>
        )}
        {tab === 'incidents' && (
          <>
            <Field label="Status">
              <select className="select-style" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
                {EVENT_STATUS_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Severity">
              <select className="select-style" value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}>
                {EVENT_SEVERITY_OPTIONS.map((o) => (
                  <option key={o.value || 'all'} value={o.value}>{o.label}</option>
                ))}
              </select>
            </Field>
          </>
        )}
        {tab === 'operator-logs' && (
          <>
            <Field label="User ID">
              <input className="input-style py-1.5 px-2" value={actorUser} placeholder="Actor user id" onChange={(e) => { setActorUser(e.target.value); setOffset(0); }} />
            </Field>
            <Field label="Action">
              <select className="select-style" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
                <option value="">All actions</option>
                {AUDIT_ACTIONS.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </Field>
            <Field label="Resource ID">
              <input className="input-style py-1.5 px-2" value={resourceId} placeholder="Resource id" onChange={(e) => { setResourceId(e.target.value); setOffset(0); }} />
            </Field>
          </>
        )}
      </div>

      <div className="flex-1 min-h-0 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden flex flex-col">
        <div className="overflow-auto flex-1 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <Loader2 size={20} className="animate-spin mr-2" /> Loading…
            </div>
          ) : error ? (
            <div className="py-12 text-center text-gray-500">{error}</div>
          ) : tab === 'alarms' && alarmRows.length === 0 ? (
            <Empty />
          ) : tab === 'incidents' && incidentRows.length === 0 ? (
            <Empty />
          ) : tab === 'operator-logs' && operatorRows.length === 0 ? (
            <Empty />
          ) : tab === 'alarms' ? (
            <table className="w-full text-sm text-left min-w-[56rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase sticky top-0">
                <tr>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Camera</th>
                  <th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2">Severity</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Alarm state</th>
                  <th className="px-3 py-2">Title</th>
                </tr>
              </thead>
              <tbody>
                {alarmRows.map((row) => (
                  <tr key={row.event_id} className="border-t border-gray-100 dark:border-gray-700">
                    <td className="px-3 py-2 whitespace-nowrap">{formatOccurredAt(row.occurred_at)}</td>
                    <td className="px-3 py-2">{row.camera_uid || row.camera_id}</td>
                    <td className="px-3 py-2">{sourceTypeLabel(row.source_type)}</td>
                    <td className="px-3 py-2">{row.severity}</td>
                    <td className="px-3 py-2">{row.status}</td>
                    <td className="px-3 py-2">{row.alarm_state}</td>
                    <td className="px-3 py-2 truncate max-w-xs">{row.title}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : tab === 'incidents' ? (
            <table className="w-full text-sm text-left min-w-[56rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase sticky top-0">
                <tr>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Camera</th>
                  <th className="px-3 py-2">Title</th>
                  <th className="px-3 py-2">Type</th>
                  <th className="px-3 py-2">Severity</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">State</th>
                </tr>
              </thead>
              <tbody>
                {incidentRows.map((row) => (
                  <tr key={row.event_id} className="border-t border-gray-100 dark:border-gray-700">
                    <td className="px-3 py-2 whitespace-nowrap">{formatOccurredAt(row.occurred_at)}</td>
                    <td className="px-3 py-2">{row.camera_uid || row.camera_id}</td>
                    <td className="px-3 py-2">
                      <div className="font-medium truncate max-w-xs">{row.title}</div>
                      <div className="text-xs text-gray-500 truncate max-w-md">{row.message}</div>
                    </td>
                    <td className="px-3 py-2">{sourceTypeLabel(row.source_type)}</td>
                    <td className="px-3 py-2">{row.severity}</td>
                    <td className="px-3 py-2">{row.status}</td>
                    <td className="px-3 py-2">{row.alarm_state}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full text-sm text-left min-w-[56rem]">
              <thead className="bg-gray-50 dark:bg-gray-700/50 text-xs uppercase sticky top-0">
                <tr>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">User</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Target</th>
                  <th className="px-3 py-2">Camera</th>
                  <th className="px-3 py-2">Result</th>
                </tr>
              </thead>
              <tbody>
                {operatorRows.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100 dark:border-gray-700">
                    <td className="px-3 py-2 whitespace-nowrap">{formatOccurredAt(row.timestamp)}</td>
                    <td className="px-3 py-2">{row.actor_username || row.actor_user_id || '—'}</td>
                    <td className="px-3 py-2">{row.action}</td>
                    <td className="px-3 py-2 truncate max-w-xs">{row.resource_label || row.resource_id || row.resource_type || '—'}</td>
                    <td className="px-3 py-2">{row.camera_id || '—'}</td>
                    <td className="px-3 py-2">{row.success ? 'success' : row.status || 'failure'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="flex items-center justify-between px-3 py-2 border-t border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300">
          <span>{pageLabel}</span>
          <div className="flex gap-2">
            <button
              type="button"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40"
              disabled={offset <= 0 || loading}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Prev
            </button>
            <button
              type="button"
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 rounded-md text-sm font-medium ${
        active
          ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
          : 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200'
      }`}
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Empty() {
  return <div className="py-12 text-center text-gray-500">No rows for these filters.</div>;
}
