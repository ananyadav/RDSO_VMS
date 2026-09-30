import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Flame, Loader2, ShieldAlert } from 'lucide-react';
import toast from 'react-hot-toast';
import CameraCard from './CameraCard';
import { waitForGo2RtcReady } from '../lib/liveProvider';
import {
  type CccHotItem,
  fetchCccHotScreen,
  formatElapsed,
} from '../lib/cccDashboardApi';
import {
  type CccAlarmItem,
  type CccAlarmMonitoring,
  fetchCccAlarmMonitoring,
  recoverCccAlarm,
} from '../lib/cccAlarmMonitoringApi';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import { authService } from '../services/authService';

interface HotScreenProps {
  recordingSchedule: Record<string, boolean>;
  onToggleRecording: (cameraId: string) => void;
}

export default function CccHotScreen({
  recordingSchedule,
  onToggleRecording,
}: HotScreenProps): React.ReactElement {
  const user = authService.getCurrentUser();
  const canLive = hasPermission(user, PERMISSIONS.LIVE_VIEW);
  const [items, setItems] = useState<CccHotItem[]>([]);
  const [alarmMon, setAlarmMon] = useState<CccAlarmMonitoring | null>(null);
  const [activeIdx, setActiveIdx] = useState(0);
  const [alarmIdx, setAlarmIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [streamsReady, setStreamsReady] = useState(false);
  const [snapBroken, setSnapBroken] = useState(false);

  const load = useCallback(async () => {
    try {
      const [hot, mon] = await Promise.all([
        fetchCccHotScreen(20),
        fetchCccAlarmMonitoring(20),
      ]);
      setItems(hot.items || []);
      setAlarmMon(mon);
      setActiveIdx((i) => (hot.items?.length ? Math.min(i, hot.items.length - 1) : 0));
      setAlarmIdx((i) => (mon.items?.length ? Math.min(i, mon.items.length - 1) : 0));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Hot Screen failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(t);
  }, [load]);

  const inAlarm = Boolean(alarmMon?.is_alarm);
  const alarmItems: CccAlarmItem[] = alarmMon?.items || [];
  const activeAlarm = inAlarm ? alarmItems[alarmIdx] || null : null;
  const activeIncident = !inAlarm ? items[activeIdx] || null : null;

  const liveCameraId = inAlarm
    ? activeAlarm?.camera_authorized
      ? activeAlarm.camera_id
      : null
    : activeIncident?.camera_authorized
      ? activeIncident.camera_id
      : null;

  useEffect(() => {
    setSnapBroken(false);
    if (!liveCameraId || !canLive) {
      setStreamsReady(false);
      return;
    }
    let cancelled = false;
    void waitForGo2RtcReady().then((ok) => {
      if (!cancelled) setStreamsReady(ok);
    });
    return () => {
      cancelled = true;
    };
  }, [liveCameraId, canLive]);

  const onRecover = async (eventId: string) => {
    try {
      const res = await recoverCccAlarm(eventId);
      setAlarmMon(res.monitoring);
      setAlarmIdx(0);
      toast.success(
        res.monitoring.is_alarm
          ? 'Alarm recovered — next queued alarm shown'
          : 'Alarm recovered — monitoring NORMAL',
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Recover failed');
    }
  };

  if (loading && items.length === 0 && !alarmMon) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
        <Loader2 className="animate-spin" size={16} /> Loading Hot Screen…
      </div>
    );
  }

  const stateLabel = alarmMon?.monitoring_label || (inAlarm ? 'ALARM' : 'NORMAL / MONITORING');
  const snapPath = activeAlarm?.snapshot?.frame_jpeg_path;

  return (
    <div
      className="flex flex-col lg:flex-row gap-3 min-h-0 h-full"
      data-testid="ccc-hot-screen"
      data-rdso="18.6.15.4,18.6.22.9"
      data-monitoring-state={alarmMon?.monitoring_state || 'MONITORING'}
    >
      <div className="w-full lg:w-80 flex-shrink-0 flex flex-col gap-2 min-h-0">
        <div
          className={`flex items-center gap-2 text-sm font-semibold ${
            inAlarm ? 'text-red-700 dark:text-red-400' : 'text-emerald-700 dark:text-emerald-400'
          }`}
        >
          {inAlarm ? <ShieldAlert size={16} /> : <Flame size={16} />}
          CCC Monitoring: {stateLabel}
        </div>
        <p className="text-[10px] text-gray-500">
          {inAlarm
            ? 'Active alarms — priority DESC. Live via VMS → /media/go2rtc only.'
            : 'NORMAL monitoring. Critical incident Hot Screen when no active alarm.'}
        </p>

        {inAlarm ? (
          <ul className="border border-red-300 dark:border-red-800 rounded divide-y divide-red-200 dark:divide-red-900 max-h-[50vh] overflow-auto text-xs">
            {alarmItems.map((it, i) => (
              <li key={it.event_id}>
                <button
                  type="button"
                  className={`w-full text-left p-2 hover:bg-red-50 dark:hover:bg-red-950/30 ${
                    i === alarmIdx ? 'bg-red-50 dark:bg-red-950/40 border-l-2 border-red-600' : ''
                  }`}
                  onClick={() => setAlarmIdx(i)}
                >
                  <div className="font-semibold truncate">
                    P{it.priority} · {it.title}
                  </div>
                  <div className="text-gray-500">
                    {it.severity} · {it.type || it.source_type} ·{' '}
                    {it.affected_zone?.unknown
                      ? 'zone unknown'
                      : it.affected_zone?.zone || '—'}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <ul className="border border-gray-200 dark:border-gray-700 rounded divide-y divide-gray-200 dark:divide-gray-700 max-h-[50vh] overflow-auto text-xs">
            {items.map((it, i) => (
              <li key={it.incident_id}>
                <button
                  type="button"
                  className={`w-full text-left p-2 hover:bg-red-50 dark:hover:bg-red-950/30 ${
                    i === activeIdx ? 'bg-red-50 dark:bg-red-950/40 border-l-2 border-red-600' : ''
                  }`}
                  onClick={() => setActiveIdx(i)}
                >
                  <div className="font-semibold truncate">
                    P{it.priority} · {it.title}
                  </div>
                  <div className="text-gray-500">
                    {it.status} · {formatElapsed(it.elapsed_seconds)}
                  </div>
                </button>
              </li>
            ))}
            {items.length === 0 ? (
              <li className="p-4 text-center text-gray-500">No critical situations</li>
            ) : null}
          </ul>
        )}
      </div>

      <div className="flex-1 min-h-[240px] flex flex-col gap-2">
        {inAlarm && activeAlarm ? (
          <>
            <div className="rounded border border-red-300 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20 p-2 text-xs space-y-1">
              <div className="text-sm font-bold">{activeAlarm.title}</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                <span>
                  Zone:{' '}
                  {activeAlarm.affected_zone?.unknown
                    ? 'unknown (no location association)'
                    : activeAlarm.affected_zone?.zone || '—'}
                </span>
                <span>
                  Type: {activeAlarm.type || activeAlarm.source_type || '—'}
                </span>
                <span>
                  Severity: {activeAlarm.severity} · P{activeAlarm.priority}
                </span>
                <span>State: {activeAlarm.state || 'ALARM'}</span>
                <span>Time: {activeAlarm.occurred_at || '—'}</span>
                <span>
                  Camera:{' '}
                  {activeAlarm.camera_authorized && activeAlarm.camera_id
                    ? `${activeAlarm.camera_response?.camera_name || activeAlarm.camera_id}${
                        activeAlarm.ptz ? ' (PTZ)' : ''
                      }`
                    : activeAlarm.camera_authorized === false
                      ? 'restricted (ACL)'
                      : '—'}
                </span>
                <span className="col-span-2 text-gray-500">
                  Selection: {activeAlarm.selection_reason || '—'} · Live Video via VMS
                </span>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  className="underline text-amber-800 font-medium"
                  onClick={() => void onRecover(activeAlarm.event_id)}
                >
                  Recover / reset
                </button>
                {activeAlarm.live_href ? (
                  <Link className="underline" to={activeAlarm.live_href}>
                    Full Live
                  </Link>
                ) : null}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 flex-1 min-h-[200px]">
              <div className="md:col-span-2 bg-black rounded overflow-hidden relative border border-gray-800 min-h-[200px]">
                {canLive && activeAlarm.camera_id && activeAlarm.camera_authorized ? (
                  <CameraCard
                    camera={{
                      id: activeAlarm.camera_id,
                      name: activeAlarm.camera_response?.camera_name || activeAlarm.title,
                      displayName: activeAlarm.camera_response?.camera_name || activeAlarm.title,
                      online: true,
                    }}
                    eagerLive
                    streamsReady={streamsReady}
                    liveActive
                    isRecording={Boolean(recordingSchedule[activeAlarm.camera_id])}
                    onToggleRecording={onToggleRecording}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-gray-400 text-sm p-4 text-center">
                    {!canLive
                      ? 'Live View permission required'
                      : !activeAlarm.camera_authorized
                        ? 'Camera not authorized for this operator (ACL)'
                        : 'No response camera resolved'}
                  </div>
                )}
              </div>
              <div className="bg-gray-900 rounded border border-gray-800 overflow-hidden flex flex-col min-h-[120px]">
                <div className="text-[10px] text-gray-400 px-2 py-1 border-b border-gray-800">
                  On-demand snapshot (VMS /media)
                </div>
                {canLive && snapPath && !snapBroken ? (
                  <img
                    src={`${snapPath}&t=${Date.now()}`}
                    alt="Alarm snapshot"
                    className="object-contain w-full flex-1 bg-black"
                    onError={() => setSnapBroken(true)}
                  />
                ) : (
                  <div className="flex-1 flex items-center justify-center text-gray-500 text-[11px] p-2 text-center">
                    {!canLive
                      ? 'Live View required'
                      : snapBroken
                        ? 'Snapshot unavailable (stream idle)'
                        : 'No snapshot path'}
                  </div>
                )}
              </div>
            </div>
            <p className="text-[10px] text-gray-500">
              Path: CCC → VMS → /media/go2rtc — direct camera RTSP forbidden
              {activeAlarm.direct_camera_rtsp === false ? ' · confirmed' : ''}
            </p>
          </>
        ) : !activeIncident ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm border border-dashed rounded">
            Monitoring NORMAL — queue empty
          </div>
        ) : (
          <>
            <div className="rounded border border-red-300 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20 p-2 text-xs space-y-1">
              <div className="text-sm font-bold">{activeIncident.title}</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                <span>Location: {activeIncident.location || '—'}</span>
                <span>
                  Severity: {activeIncident.severity} · P{activeIncident.priority}
                </span>
                <span>Status: {activeIncident.status}</span>
                <span>
                  Assignee: {activeIncident.assignee_group || activeIncident.assignee_user_name || '—'}
                </span>
                <span>Elapsed: {formatElapsed(activeIncident.elapsed_seconds)}</span>
                <span>
                  Camera:{' '}
                  {activeIncident.camera_authorized && activeIncident.camera_id
                    ? activeIncident.camera_id
                    : activeIncident.camera_authorized === false
                      ? 'restricted (ACL)'
                      : '—'}
                </span>
              </div>
              <div className="flex flex-wrap gap-2 pt-1">
                <Link
                  className="underline text-emerald-700 font-medium"
                  to={activeIncident.workspace_href || '/ccc?tab=incidents'}
                >
                  Incident workspace
                </Link>
                {activeIncident.live_href ? (
                  <Link className="underline" to={activeIncident.live_href}>
                    Full Live
                  </Link>
                ) : null}
                {activeIncident.playback_href ? (
                  <Link className="underline" to={activeIncident.playback_href}>
                    Playback
                  </Link>
                ) : null}
              </div>
            </div>

            <div className="flex-1 min-h-[200px] bg-black rounded overflow-hidden relative border border-gray-800">
              {canLive && activeIncident.camera_id && activeIncident.camera_authorized ? (
                <CameraCard
                  camera={{
                    id: activeIncident.camera_id,
                    name: activeIncident.title,
                    displayName: activeIncident.title,
                    online: true,
                  }}
                  eagerLive
                  streamsReady={streamsReady}
                  liveActive
                  isRecording={Boolean(recordingSchedule[activeIncident.camera_id])}
                  onToggleRecording={onToggleRecording}
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-gray-400 text-sm p-4 text-center">
                  {!canLive
                    ? 'Live View permission required'
                    : !activeIncident.camera_authorized
                      ? 'Camera not authorized for this operator (ACL)'
                      : 'No linked camera'}
                </div>
              )}
            </div>
            <p className="text-[10px] text-gray-500">
              Path: CCC → VMS live — direct camera RTSP forbidden
              {activeIncident.direct_camera_rtsp === false ? ' · confirmed' : ''}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
