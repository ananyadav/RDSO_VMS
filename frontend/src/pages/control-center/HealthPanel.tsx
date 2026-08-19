import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ChevronDown, Loader2 } from 'lucide-react';
import Card from '../../components/Card';
import ConfirmModal from '../../components/control-center/ConfirmModal';
import {
  ControlCenterRequestError,
  fetchGo2RtcStatus,
  fetchHealthStatus,
  postGo2RtcAction,
  type Go2RtcStatus,
  type HealthStatus,
} from '../../lib/controlCenterApi';

type PendingAction = {
  title: string;
  body: string;
  label: string;
  path: string;
};

export default function HealthPanel(): React.ReactElement {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [go2rtc, setGo2rtc] = useState<Go2RtcStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, g] = await Promise.all([fetchHealthStatus(), fetchGo2RtcStatus()]);
      setHealth(h);
      setGo2rtc(g);
    } catch (err) {
      const message = err instanceof ControlCenterRequestError ? err.message : 'Could not load system health.';
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      await postGo2RtcAction(pending.path);
      toast.success('Worker command completed.');
      setPending(null);
      await load();
    } catch (err) {
      toast.error(err instanceof ControlCenterRequestError ? err.message : 'Worker command failed.');
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-400">
        <Loader2 className="animate-spin mr-2" size={20} /> Loading system health…
      </div>
    );
  }

  if (error && !health && !go2rtc) {
    return <p className="py-12 text-center text-gray-500">{error}</p>;
  }

  const workers = go2rtc?.workers || [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card>
          <h3 className="font-semibold text-white mb-2">Backend</h3>
          <ul className="text-sm text-gray-300 space-y-1">
            <li>Status: {health?.ready ? 'Ready' : 'Not ready'}</li>
            <li>Phase: {health?.phase || '—'}</li>
          </ul>
        </Card>
        <Card>
          <h3 className="font-semibold text-white mb-2">MongoDB</h3>
          <p className="text-sm text-gray-300">{health?.mongodb ? 'Connected' : 'Unavailable'}</p>
        </Card>
        <Card>
          <h3 className="font-semibold text-white mb-2">Cameras</h3>
          <p className="text-2xl font-bold text-white">{health?.cameraCount ?? '—'}</p>
        </Card>
      </div>

      <Card>
        <h3 className="font-semibold text-white mb-3">go2rtc workers</h3>
        {go2rtc ? (
          <>
            <p className="text-sm text-gray-400 mb-3">
              {go2rtc.running ? 'Running' : 'Stopped'}
              {typeof go2rtc.streamCount === 'number' ? ` · ${go2rtc.streamCount} streams` : ''}
              {typeof go2rtc.cameraCount === 'number' ? ` · ${go2rtc.cameraCount} cameras` : ''}
              {go2rtc.binaryFound === false ? ' · binary not found' : ''}
            </p>
            {workers.length === 0 ? (
              <p className="text-sm text-gray-500">No worker records returned.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left text-gray-300 min-w-[36rem]">
                  <thead className="text-xs uppercase text-gray-500">
                    <tr>
                      <th className="py-2 pr-3">Worker</th>
                      <th className="py-2 pr-3">State</th>
                      <th className="py-2 pr-3">Cameras</th>
                      <th className="py-2 pr-3">Live streams</th>
                      <th className="py-2 pr-3">Ports</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workers.map((w) => (
                      <tr key={w.workerId} className="border-t border-gray-700">
                        <td className="py-2 pr-3 font-medium text-white">w{w.workerId}{w.pm2Name ? ` · ${w.pm2Name}` : ''}</td>
                        <td className={w.running ? 'text-green-400' : 'text-red-400'}>{w.running ? 'Running' : 'Stopped'}</td>
                        <td className="py-2 pr-3">{w.assignedCameraCount ?? '—'}</td>
                        <td className="py-2 pr-3">{w.liveStreamCount ?? '—'}</td>
                        <td className="py-2 pr-3 text-gray-400">
                          {[w.apiPort, w.rtspPort, w.webrtcPort].filter((n) => n != null).join(' / ') || '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-gray-500">Worker status unavailable.</p>
        )}
      </Card>

      <div className="border border-gray-700 rounded-lg">
        <button
          type="button"
          className="w-full flex items-center justify-between px-4 py-3 text-sm text-gray-300 hover:text-white"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          Advanced worker controls
          <ChevronDown size={16} className={showAdvanced ? 'rotate-180' : ''} />
        </button>
        {showAdvanced && (
          <div className="px-4 pb-4 space-y-3">
            <p className="text-xs text-gray-500">
              These actions can interrupt live streams. They are not run automatically.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-secondary px-3 py-2 text-sm w-auto"
                onClick={() => setPending({
                  title: 'Start media workers?',
                  body: 'This starts go2rtc workers if they are stopped.',
                  label: 'Start',
                  path: '/api/go2rtc/start',
                })}
              >
                Start
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-2 text-sm w-auto"
                onClick={() => setPending({
                  title: 'Stop all media workers?',
                  body: 'Live View streams will go down until workers are started again.',
                  label: 'Stop',
                  path: '/api/go2rtc/stop',
                })}
              >
                Stop
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-2 text-sm w-auto"
                onClick={() => setPending({
                  title: 'Heal media workers?',
                  body: 'Unhealthy workers may restart. Existing streams on those workers may reconnect.',
                  label: 'Heal',
                  path: '/api/go2rtc/workers/heal',
                })}
              >
                Heal workers
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-2 text-sm w-auto"
                onClick={() => setPending({
                  title: 'Reload media workers?',
                  body: 'Existing streams may reconnect.',
                  label: 'Reload',
                  path: '/api/go2rtc/reload',
                })}
              >
                Reload
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-2 text-sm w-auto"
                onClick={() => setPending({
                  title: 'Rebalance cameras across workers?',
                  body: 'Camera assignments will change. Existing streams may reconnect.',
                  label: 'Rebalance',
                  path: '/api/go2rtc/workers/rebalance',
                })}
              >
                Rebalance
              </button>
              {workers.map((w) => (
                <button
                  key={w.workerId}
                  type="button"
                  className="btn-secondary px-3 py-2 text-sm w-auto"
                  onClick={() => setPending({
                    title: `Sync Media Worker ${w.workerId}?`,
                    body: 'Existing streams on this worker may reconnect.',
                    label: 'Sync',
                    path: `/api/go2rtc/workers/${w.workerId}/sync`,
                  })}
                >
                  Sync w{w.workerId}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <ConfirmModal
        open={Boolean(pending)}
        title={pending?.title || ''}
        body={<p>{pending?.body}</p>}
        confirmLabel={pending?.label || 'Confirm'}
        danger
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => void runAction()}
      />
    </div>
  );
}
