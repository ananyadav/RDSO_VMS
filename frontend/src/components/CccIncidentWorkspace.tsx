import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';
import {
  type CccCommunication,
  type CccCompliance,
  type CccMessageTemplate,
  acknowledgeCccCommunication,
  fetchCccCompliance,
  fetchCccCommunications,
  fetchCccMessageTemplates,
  sendCccCommunication,
} from '../lib/cccDashboardApi';
import {
  type CccEventLogRow,
  type CccIncident,
  type CccSopWorkflow,
  assignCccIncident,
  createCccIncident,
  createCccSopWorkflow,
  fetchCccEventLog,
  fetchCccIncident,
  fetchCccSopWorkflows,
  incidentLiveHref,
  incidentPlaybackHref,
  processCccEscalations,
  updateCccIncident,
  updateCccSopWorkflow,
} from '../lib/cccIncidentsApi';

type Mode = 'event-log' | 'incidents' | 'sop';

export default function CccIncidentWorkspace({
  initialMode = 'event-log',
}: {
  initialMode?: Mode;
}): React.ReactElement {
  const user = authService.getCurrentUser();
  const canEditSop = isOpsAdminUser(user);
  const [mode, setMode] = useState<Mode>(initialMode);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<CccEventLogRow[]>([]);
  const [incidents, setIncidents] = useState<CccIncident[]>([]);
  const [selected, setSelected] = useState<CccIncident | null>(null);
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [q, setQ] = useState('');
  const [comment, setComment] = useState('');
  const [manualTitle, setManualTitle] = useState('');
  const [sops, setSops] = useState<CccSopWorkflow[]>([]);
  const [sopName, setSopName] = useState('Default CCC Response');
  const [sopSteps, setSopSteps] = useState('Acknowledge\nDispatch responder\nVerify on Live View\nClose');
  const [compliance, setCompliance] = useState<CccCompliance | null>(null);
  const [comms, setComms] = useState<CccCommunication[]>([]);
  const [templates, setTemplates] = useState<CccMessageTemplate[]>([]);
  const [commBody, setCommBody] = useState('');
  const [commGroup, setCommGroup] = useState('responder');
  const [commTemplateId, setCommTemplateId] = useState('');

  useEffect(() => {
    setMode(initialMode);
  }, [initialMode]);

  const loadLog = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchCccEventLog({
        limit: 50,
        offset: 0,
        critical_only: criticalOnly,
        q: q.trim() || undefined,
      });
      setRows(data.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Event log failed');
    } finally {
      setLoading(false);
    }
  }, [criticalOnly, q]);

  const loadIncidents = useCallback(async () => {
    setLoading(true);
    try {
      const { fetchCccIncidents } = await import('../lib/cccIncidentsApi');
      const data = await fetchCccIncidents({
        limit: 50,
        critical_only: criticalOnly,
        q: q.trim() || undefined,
      });
      setIncidents(data.items || []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Incidents failed');
    } finally {
      setLoading(false);
    }
  }, [criticalOnly, q]);

  const loadSops = useCallback(async () => {
    try {
      setSops(await fetchCccSopWorkflows());
    } catch {
      setSops([]);
    }
  }, []);

  useEffect(() => {
    if (mode === 'event-log') void loadLog();
    if (mode === 'incidents') void loadIncidents();
    if (mode === 'sop') void loadSops();
  }, [mode, loadLog, loadIncidents, loadSops]);

  const promote = async (eventId: string) => {
    try {
      const inc = await createCccIncident({ event_id: eventId });
      toast.success(`Incident ${inc.id.slice(-6)} created`);
      setSelected(inc);
      setMode('incidents');
      void loadIncidents();
    } catch (err) {
      const e = err as Error & { duplicate?: boolean };
      toast.error(e.duplicate ? `Duplicate: ${e.message}` : e.message || 'Promote failed');
    }
  };

  const createManual = async () => {
    if (!manualTitle.trim()) return toast.error('Title required');
    try {
      const inc = await createCccIncident({
        title: manualTitle.trim(),
        location: '',
        severity: 'warning',
        priority: 3,
      });
      toast.success('Manual incident created');
      setManualTitle('');
      setSelected(inc);
      setMode('incidents');
      void loadIncidents();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Create failed');
    }
  };

  const openIncident = async (id: string) => {
    try {
      const inc = await fetchCccIncident(id);
      setSelected(inc);
      const [comp, messages, tpls] = await Promise.all([
        fetchCccCompliance(id).catch(() => null),
        fetchCccCommunications(id).then((r) => r.items || []).catch(() => []),
        fetchCccMessageTemplates().then((r) => r.items || []).catch(() => []),
      ]);
      setCompliance(comp);
      setComms(messages);
      setTemplates(tpls);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Load failed');
    }
  };

  const advanceSopStep = async () => {
    if (!selected) return;
    const next = (selected.sop_step_index || 0) + 1;
    try {
      const inc = await updateCccIncident(selected.id, { sop_step_index: next });
      setSelected(inc);
      setCompliance(await fetchCccCompliance(inc.id));
      toast.success('SOP step advanced');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'SOP step failed');
    }
  };

  const sendComm = async () => {
    if (!selected) return;
    if (!commBody.trim() && !commTemplateId) {
      toast.error('Message body or template required');
      return;
    }
    try {
      await sendCccCommunication({
        incident_id: selected.id,
        body: commBody.trim() || undefined,
        template_id: commTemplateId || undefined,
        recipient_group: commGroup.trim() || 'responder',
      });
      setCommBody('');
      setComms((await fetchCccCommunications(selected.id)).items || []);
      toast.success('Internal CCC message logged (no SMS/email/DMR)');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Send failed');
    }
  };

  const ackComm = async (id: string) => {
    try {
      await acknowledgeCccCommunication(id);
      if (selected) setComms((await fetchCccCommunications(selected.id)).items || []);
      toast.success('Acknowledged');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Ack failed');
    }
  };

  const saveComment = async () => {
    if (!selected || !comment.trim()) return;
    try {
      const inc = await updateCccIncident(selected.id, { comment: comment.trim() });
      setSelected(inc);
      setComment('');
      toast.success('Comment added');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Update failed');
    }
  };

  const setStatus = async (status: string) => {
    if (!selected) return;
    try {
      setSelected(await updateCccIncident(selected.id, { status }));
      toast.success(`Status → ${status}`);
      void loadIncidents();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Status failed');
    }
  };

  const redirectRpf = async () => {
    if (!selected) return;
    try {
      const inc = await assignCccIncident(selected.id, {
        assignee_group: 'RPF',
        reason: 'manual_redirect_rpf',
      });
      setSelected(inc);
      toast.success('Redirected to RPF');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Assign failed');
    }
  };

  const redirectResponder = async () => {
    if (!selected) return;
    try {
      const inc = await assignCccIncident(selected.id, {
        assignee_group: 'responder',
        assignee_user_name: user?.name || 'operator',
        reason: 'manual_redirect_responder',
      });
      setSelected(inc);
      toast.success('Assigned to responder');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Assign failed');
    }
  };

  const runEscalations = async () => {
    try {
      const r = await processCccEscalations();
      toast.success(`Escalations: ${r.escalated ?? 0} (DMR/Tetra not used)`);
      void loadIncidents();
      if (selected) void openIncident(selected.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Escalation failed');
    }
  };

  const saveSop = async () => {
    if (!canEditSop) return toast.error('Admin only');
    const steps = sopSteps
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      if (sops[0]) {
        await updateCccSopWorkflow(sops[0].id, {
          name: sopName,
          steps,
          escalation_rules: [
            {
              id: 'auto_rpf',
              delay_seconds: 120,
              min_priority: 4,
              location_prefix: '',
              assign_group: 'RPF',
              from_statuses: ['open', 'assigned', 'in_progress'],
              enabled: true,
            },
          ],
          enabled: true,
        });
        toast.success('SOP updated (hot-reload, no restart)');
      } else {
        await createCccSopWorkflow({
          name: sopName,
          steps,
          escalation_rules: [
            {
              id: 'auto_rpf',
              delay_seconds: 120,
              min_priority: 4,
              assign_group: 'RPF',
              from_statuses: ['open', 'assigned', 'in_progress'],
              enabled: true,
            },
          ],
          enabled: true,
        });
        toast.success('SOP created (hot-reload)');
      }
      void loadSops();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'SOP save failed');
    }
  };

  return (
    <div className="space-y-3" data-testid="ccc-incident-workspace">
      <div className="flex flex-wrap gap-1">
        {(
          [
            ['event-log', 'Event Log'],
            ['incidents', 'Incidents'],
            ['sop', 'SOP / Escalation'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setMode(id)}
            className={`px-2.5 py-1 rounded text-xs font-medium ${
              mode === id
                ? 'bg-sky-700 text-white'
                : 'border border-gray-300 dark:border-gray-600'
            }`}
          >
            {label}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={criticalOnly}
            onChange={(e) => setCriticalOnly(e.target.checked)}
          />
          Critical only
        </label>
      </div>

      <div className="flex gap-1">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter…"
          className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
        />
        <button
          type="button"
          className="text-xs px-2 py-1 rounded bg-gray-800 text-white"
          onClick={() => {
            if (mode === 'event-log') void loadLog();
            if (mode === 'incidents') void loadIncidents();
          }}
        >
          Refresh
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Loader2 className="animate-spin" size={14} /> Loading…
        </div>
      )}

      {mode === 'event-log' && (
        <div className="overflow-auto border border-gray-200 dark:border-gray-700 rounded max-h-[50vh]">
          <table className="w-full text-[11px]">
            <thead className="bg-gray-100 dark:bg-gray-800 sticky top-0">
              <tr>
                <th className="text-left p-1.5">Time</th>
                <th className="text-left p-1.5">Camera/Loc</th>
                <th className="text-left p-1.5">Type/Sev/Pri</th>
                <th className="text-left p-1.5">Incident</th>
                <th className="text-left p-1.5">Assignee</th>
                <th className="text-left p-1.5">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.event_id}
                  className={`border-t border-gray-200 dark:border-gray-700 ${
                    r.critical ? 'bg-red-50 dark:bg-red-950/30' : ''
                  }`}
                >
                  <td className="p-1.5 whitespace-nowrap">{r.event_time || '—'}</td>
                  <td className="p-1.5">
                    <div className="truncate max-w-[9rem]">{r.camera_id || r.camera_uid || '—'}</div>
                    <div className="text-gray-500 truncate">{r.location || ''}</div>
                  </td>
                  <td className="p-1.5">
                    {r.source_type} / {r.severity} / P{r.priority}
                    {r.critical ? ' ★' : ''}
                  </td>
                  <td className="p-1.5">{r.incident_status || '—'}</td>
                  <td className="p-1.5">
                    {r.assignee_group || r.assignee_user_name || '—'}
                  </td>
                  <td className="p-1.5 space-x-1 whitespace-nowrap">
                    {!r.incident_id ? (
                      <button
                        type="button"
                        className="underline text-emerald-700"
                        onClick={() => void promote(r.event_id)}
                      >
                        Promote
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="underline"
                        onClick={() => {
                          setMode('incidents');
                          void openIncident(r.incident_id!);
                        }}
                      >
                        Open
                      </button>
                    )}
                    {r.live_href ? (
                      <Link className="underline" to={r.live_href}>
                        Live
                      </Link>
                    ) : null}
                    {r.playback_href ? (
                      <Link className="underline" to={r.playback_href}>
                        Playback
                      </Link>
                    ) : null}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && !loading ? (
                <tr>
                  <td colSpan={6} className="p-4 text-center text-gray-500">
                    No events
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}

      {mode === 'incidents' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="space-y-2">
            <div className="flex gap-1">
              <input
                value={manualTitle}
                onChange={(e) => setManualTitle(e.target.value)}
                placeholder="Manual incident title…"
                className="flex-1 text-xs px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
              <button
                type="button"
                className="text-xs px-2 py-1 rounded bg-emerald-700 text-white"
                onClick={() => void createManual()}
              >
                Create
              </button>
            </div>
            <ul className="border border-gray-200 dark:border-gray-700 rounded divide-y divide-gray-200 dark:divide-gray-700 max-h-[40vh] overflow-auto text-xs">
              {incidents.map((inc) => (
                <li key={inc.id}>
                  <button
                    type="button"
                    className={`w-full text-left p-2 hover:bg-gray-50 dark:hover:bg-gray-800 ${
                      selected?.id === inc.id ? 'bg-sky-50 dark:bg-sky-950/40' : ''
                    } ${inc.critical ? 'border-l-2 border-red-500' : ''}`}
                    onClick={() => void openIncident(inc.id)}
                  >
                    <div className="font-semibold truncate">{inc.title}</div>
                    <div className="text-gray-500">
                      {inc.status} · P{inc.priority} · {inc.assignee_group || 'unassigned'}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="border border-gray-200 dark:border-gray-700 rounded p-2 text-xs space-y-2 min-h-[12rem]">
            {!selected ? (
              <p className="text-gray-500">Select an incident for timeline / actions</p>
            ) : (
              <>
                <div>
                  <h3 className="font-bold text-sm">{selected.title}</h3>
                  <p className="text-gray-500">
                    {selected.location || '—'} · {selected.status} · P{selected.priority}{' '}
                    {selected.critical ? '(critical)' : ''}
                  </p>
                  <p className="text-gray-500">
                    Assignee: {selected.assignee_group || selected.assignee_user_name || '—'}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1">
                  {['in_progress', 'resolved', 'closed'].map((st) => (
                    <button
                      key={st}
                      type="button"
                      className="px-2 py-0.5 border rounded"
                      onClick={() => void setStatus(st)}
                    >
                      {st}
                    </button>
                  ))}
                  <button type="button" className="px-2 py-0.5 border rounded" onClick={() => void redirectResponder()}>
                    → Responder
                  </button>
                  <button type="button" className="px-2 py-0.5 border rounded" onClick={() => void redirectRpf()}>
                    → RPF
                  </button>
                </div>
                {selected.linked_camera_ids?.[0] ? (
                  <div className="space-x-2">
                    <Link className="underline text-emerald-700" to={incidentLiveHref(selected.linked_camera_ids[0])}>
                      Open Live (VMS)
                    </Link>
                    <Link
                      className="underline text-emerald-700"
                      to={incidentPlaybackHref(selected.linked_camera_ids[0])}
                    >
                      Open Playback (VMS)
                    </Link>
                  </div>
                ) : null}
                <div>
                  <div className="font-semibold mb-1">Timeline</div>
                  <ol className="space-y-1 max-h-40 overflow-auto">
                    {[...(selected.timeline || [])].reverse().map((t, i) => (
                      <li key={`${t.at}-${i}`} className="border-l-2 border-gray-400 pl-2">
                        <div className="text-gray-500">{t.at}</div>
                        <div>
                          <span className="font-medium">{t.type}</span>
                          {t.message ? ` — ${t.message}` : ''}
                          {t.actor?.name ? ` (${t.actor.name})` : ''}
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
                {compliance ? (
                  <div className="border-t border-gray-200 dark:border-gray-700 pt-2 space-y-1">
                    <div className="font-semibold">
                      SOP compliance · {compliance.state}
                      {compliance.overdue ? ' (overdue)' : ''}
                    </div>
                    <div className="text-gray-500">
                      {compliance.completed_steps}/{compliance.total_steps} steps · assignee{' '}
                      {compliance.assigned_group || compliance.assigned_user_id || '—'}
                      {compliance.due_at ? ` · due ${compliance.due_at}` : ''}
                    </div>
                    <ul className="space-y-0.5">
                      {(compliance.steps || []).map((s, i) => (
                        <li key={i} className={s.done ? 'text-emerald-700' : 'text-gray-600'}>
                          {s.done ? '✓' : '○'} {s.title}
                        </li>
                      ))}
                    </ul>
                    {compliance.pending_steps > 0 ? (
                      <button type="button" className="px-2 py-0.5 border rounded" onClick={() => void advanceSopStep()}>
                        Mark next step done
                      </button>
                    ) : null}
                  </div>
                ) : null}
                <div className="border-t border-gray-200 dark:border-gray-700 pt-2 space-y-1">
                  <div className="font-semibold">Internal CCC communications</div>
                  <p className="text-[10px] text-gray-500">
                    In-app messages/receipts only — not SMS, email, DMR or Tetra.
                  </p>
                  {templates.length > 0 ? (
                    <select
                      className="w-full px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
                      value={commTemplateId}
                      onChange={(e) => {
                        setCommTemplateId(e.target.value);
                        const t = templates.find((x) => x.id === e.target.value);
                        if (t) setCommBody(t.body);
                      }}
                    >
                      <option value="">Template…</option>
                      {templates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  ) : null}
                  <input
                    value={commGroup}
                    onChange={(e) => setCommGroup(e.target.value)}
                    placeholder="Recipient group (e.g. responder)"
                    className="w-full px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
                  />
                  <textarea
                    value={commBody}
                    onChange={(e) => setCommBody(e.target.value)}
                    rows={2}
                    placeholder="Message body…"
                    className="w-full px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
                  />
                  <button type="button" className="px-2 py-1 rounded bg-sky-800 text-white" onClick={() => void sendComm()}>
                    Send / log
                  </button>
                  <ul className="space-y-1 max-h-28 overflow-auto">
                    {comms.map((c) => (
                      <li key={c.id} className="border-l-2 border-sky-500 pl-2">
                        <div>
                          {c.status} · {c.recipient_group || c.recipient_user_id} ·{' '}
                          {c.external_delivery === false ? 'internal' : ''}
                        </div>
                        <div className="truncate text-gray-500">{c.body}</div>
                        {c.status !== 'acknowledged' ? (
                          <button type="button" className="underline" onClick={() => void ackComm(c.id)}>
                            Acknowledge
                          </button>
                        ) : (
                          <span className="text-gray-500">ack {c.acknowledged_at}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="flex gap-1">
                  <input
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    placeholder="Add comment…"
                    className="flex-1 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
                  />
                  <button
                    type="button"
                    className="px-2 py-1 rounded bg-gray-800 text-white"
                    onClick={() => void saveComment()}
                  >
                    Post
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {mode === 'sop' && (
        <div className="space-y-2 max-w-xl text-xs">
          <p className="text-gray-600 dark:text-gray-300">
            SOP edits persist in Mongo and apply immediately (no restart). Escalation assigns
            responder/RPF inside CCC only — not DMR/Tetra.
          </p>
          <input
            value={sopName}
            onChange={(e) => setSopName(e.target.value)}
            disabled={!canEditSop}
            className="w-full px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
          />
          <textarea
            value={sopSteps}
            onChange={(e) => setSopSteps(e.target.value)}
            disabled={!canEditSop}
            rows={6}
            className="w-full px-2 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-transparent font-mono"
          />
          <div className="flex gap-2">
            {canEditSop ? (
              <button
                type="button"
                className="px-3 py-1.5 rounded bg-emerald-700 text-white"
                onClick={() => void saveSop()}
              >
                Save SOP
              </button>
            ) : (
              <span className="text-gray-500">Admin required to edit SOP</span>
            )}
            <button
              type="button"
              className="px-3 py-1.5 rounded border"
              onClick={() => void runEscalations()}
            >
              Process due escalations
            </button>
          </div>
          {sops[0] ? (
            <pre className="bg-gray-100 dark:bg-gray-950 p-2 rounded overflow-auto max-h-40">
              {JSON.stringify(
                {
                  id: sops[0].id,
                  steps: sops[0].steps?.length,
                  escalation_rules: sops[0].escalation_rules,
                  hot_reload: true,
                },
                null,
                2,
              )}
            </pre>
          ) : null}
        </div>
      )}
    </div>
  );
}
