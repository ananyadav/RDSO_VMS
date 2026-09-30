import React, { useCallback, useEffect, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  type CccReportKind,
  downloadCccReportCsv,
  downloadCccReportJson,
  fetchCccReport,
} from '../lib/cccReportsApi';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';

const PAGE = 50;

type Kind =
  | 'incidents'
  | 'events'
  | 'activity'
  | 'communications'
  | 'compliance';

export default function CccHistoricalReports(): React.ReactElement {
  const user = authService.getCurrentUser();
  const canActivity = isOpsAdminUser(user);

  const [kind, setKind] = useState<Kind>('incidents');
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [tz, setTz] = useState('');
  const [empty, setEmpty] = useState(false);

  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [status, setStatus] = useState('');
  const [severity, setSeverity] = useState('');
  const [priority, setPriority] = useState('');
  const [group, setGroup] = useState('');
  const [location, setLocation] = useState('');
  const [cameraId, setCameraId] = useState('');
  const [sourceType, setSourceType] = useState('');
  const [acknowledged, setAcknowledged] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [complianceState, setComplianceState] = useState('');
  const [incidentId, setIncidentId] = useState('');

  useEffect(() => {
    if (kind === 'activity' && !canActivity) setKind('incidents');
  }, [kind, canActivity]);

  const baseParams = useCallback(() => {
    const p: Record<string, string | number | undefined> = {
      from: from || undefined,
      to: to || undefined,
      limit: PAGE,
      offset,
    };
    if (kind === 'incidents') {
      p.status = status || undefined;
      p.severity = severity || undefined;
      p.priority = priority || undefined;
      p.assignee_group = group || undefined;
      p.location = location || undefined;
      p.camera_id = cameraId || undefined;
    } else if (kind === 'events') {
      p.camera_id = cameraId || undefined;
      p.source_type = sourceType || undefined;
      p.severity = severity || undefined;
      p.status = status || undefined;
      p.acknowledged = acknowledged || undefined;
    } else if (kind === 'activity') {
      p.user = actor || undefined;
      p.action = action || undefined;
      p.camera_id = cameraId || undefined;
      p.ccc_only = 'true';
    } else if (kind === 'communications') {
      p.status = status || undefined;
      p.incident_id = incidentId || undefined;
    } else if (kind === 'compliance') {
      p.state = complianceState || undefined;
    }
    return p;
  }, [
    kind,
    from,
    to,
    offset,
    status,
    severity,
    priority,
    group,
    location,
    cameraId,
    sourceType,
    acknowledged,
    actor,
    action,
    complianceState,
    incidentId,
  ]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCccReport(kind as CccReportKind, baseParams());
      setRows(data.items || []);
      setTotal(data.total || 0);
      setTz(String(data.app_timezone || ''));
      setEmpty(Boolean(data.empty) || (data.total || 0) === 0);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Report failed');
      setRows([]);
      setTotal(0);
      setEmpty(true);
    } finally {
      setLoading(false);
    }
  }, [kind, baseParams]);

  useEffect(() => {
    void load();
  }, [load]);

  const exportFmt = async (fmt: 'csv' | 'json') => {
    setExporting(true);
    try {
      const params = { ...baseParams(), offset: 0 };
      const stamp = new Date().toISOString().slice(0, 10);
      if (fmt === 'csv') {
        await downloadCccReportCsv(kind, params, `ccc-${kind}-${stamp}.csv`);
      } else {
        await downloadCccReportJson(kind, params, `ccc-${kind}-${stamp}.json`);
      }
      toast.success(`${fmt.toUpperCase()} export started`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const tabs: { id: Kind; label: string; show: boolean }[] = [
    { id: 'incidents', label: 'Incident history', show: true },
    { id: 'events', label: 'Event history', show: true },
    { id: 'activity', label: 'Activity logs', show: canActivity },
    { id: 'communications', label: 'Communications', show: true },
    { id: 'compliance', label: 'Compliance', show: true },
  ];

  const columns = rows[0] ? Object.keys(rows[0]).slice(0, 10) : [];

  return (
    <div className="space-y-3 text-xs" data-testid="ccc-historical-reports" data-rdso="18.6.22.18">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-gray-600 dark:text-gray-300">
          Historical CCC reports from persisted events/incidents/audit/comms — no duplicate stores.
          {tz ? ` · APP_TIMEZONE=${tz}` : ''}
        </p>
        <div className="flex gap-1">
          <button
            type="button"
            disabled={exporting}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-emerald-700 text-white disabled:opacity-50"
            onClick={() => void exportFmt('csv')}
          >
            <Download size={12} /> CSV
          </button>
          <button
            type="button"
            disabled={exporting}
            className="inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 disabled:opacity-50"
            onClick={() => void exportFmt('json')}
          >
            <Download size={12} /> JSON
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {tabs
          .filter((t) => t.show)
          .map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setKind(t.id);
                setOffset(0);
              }}
              className={`px-2 py-1 rounded ${
                kind === t.id
                  ? 'bg-emerald-700 text-white'
                  : 'border border-gray-300 dark:border-gray-600'
              }`}
            >
              {t.label}
            </button>
          ))}
      </div>

      <div className="flex flex-wrap gap-2 items-end">
        <label className="flex flex-col gap-0.5">
          From (date or ISO)
          <input
            type="text"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            placeholder="YYYY-MM-DD"
            className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          To
          <input
            type="text"
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="YYYY-MM-DD"
            className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          />
        </label>
        {(kind === 'incidents' || kind === 'events' || kind === 'communications') && (
          <label className="flex flex-col gap-0.5">
            Status
            <input
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
          </label>
        )}
        {(kind === 'incidents' || kind === 'events') && (
          <label className="flex flex-col gap-0.5">
            Severity
            <input
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
          </label>
        )}
        {kind === 'incidents' && (
          <>
            <label className="flex flex-col gap-0.5">
              Priority
              <input
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-16 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              Group
              <input
                value={group}
                onChange={(e) => setGroup(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              Location
              <input
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
          </>
        )}
        {(kind === 'incidents' || kind === 'events' || kind === 'activity') && (
          <label className="flex flex-col gap-0.5">
            Camera
            <input
              value={cameraId}
              onChange={(e) => setCameraId(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
          </label>
        )}
        {kind === 'events' && (
          <>
            <label className="flex flex-col gap-0.5">
              Event type
              <input
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              Ack
              <select
                value={acknowledged}
                onChange={(e) => setAcknowledged(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              >
                <option value="">Any</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </label>
          </>
        )}
        {kind === 'activity' && (
          <>
            <label className="flex flex-col gap-0.5">
              User
              <input
                value={actor}
                onChange={(e) => setActor(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              Action
              <input
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
            </label>
          </>
        )}
        {kind === 'communications' && (
          <label className="flex flex-col gap-0.5">
            Incident id
            <input
              value={incidentId}
              onChange={(e) => setIncidentId(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
          </label>
        )}
        {kind === 'compliance' && (
          <label className="flex flex-col gap-0.5">
            State
            <select
              value={complianceState}
              onChange={(e) => setComplianceState(e.target.value)}
              className="px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            >
              <option value="">Any</option>
              <option value="pending">pending</option>
              <option value="complete">complete</option>
              <option value="overdue">overdue</option>
            </select>
          </label>
        )}
        <button
          type="button"
          className="px-3 py-1.5 rounded bg-gray-800 text-white"
          onClick={() => {
            setOffset(0);
            void load();
          }}
        >
          Apply
        </button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500 py-6 justify-center">
          <Loader2 className="animate-spin" size={16} /> Loading…
        </div>
      ) : empty || rows.length === 0 ? (
        <div className="border border-dashed rounded p-6 text-center text-gray-500">
          No records for this filter — empty history (not fabricated).
        </div>
      ) : (
        <div className="overflow-auto border border-gray-200 dark:border-gray-700 rounded max-h-[50vh]">
          <table className="min-w-full text-left">
            <thead className="bg-gray-50 dark:bg-gray-950 sticky top-0">
              <tr>
                {columns.map((c) => (
                  <th key={c} className="px-2 py-1 font-semibold whitespace-nowrap">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-gray-100 dark:border-gray-800">
                  {columns.map((c) => (
                    <td key={c} className="px-2 py-1 max-w-[14rem] truncate">
                      {String(r[c] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={offset <= 0}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
        >
          Prev
        </button>
        <span className="text-gray-500">
          {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button
          type="button"
          disabled={offset + PAGE >= total}
          className="px-2 py-1 border rounded disabled:opacity-40"
          onClick={() => setOffset((o) => o + PAGE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
