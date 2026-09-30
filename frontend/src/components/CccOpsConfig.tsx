import React, { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { isOpsAdminUser } from '../lib/permissions';
import { authService } from '../services/authService';
import {
  createCccGroup,
  createCccMessageTemplate,
  fetchCccGroups,
  fetchCccMessageTemplates,
  type CccGroup,
  type CccMessageTemplate,
} from '../lib/cccDashboardApi';
import {
  fetchCccZoneCameraMap,
  saveCccZoneCameraMap,
  type CccZoneCameraMapping,
} from '../lib/cccAlarmMonitoringApi';
import CccVmsIntegrations from './CccVmsIntegrations';

/** Lightweight CCC ops groups + message templates (not RBAC) + zone→camera map. */
export default function CccOpsConfig(): React.ReactElement {
  const user = authService.getCurrentUser();
  const canAdmin = isOpsAdminUser(user);
  const [groups, setGroups] = useState<CccGroup[]>([]);
  const [templates, setTemplates] = useState<CccMessageTemplate[]>([]);
  const [mappings, setMappings] = useState<CccZoneCameraMapping[]>([]);
  const [gName, setGName] = useState('');
  const [gKind, setGKind] = useState('responder');
  const [tName, setTName] = useState('');
  const [tBody, setTBody] = useState('');
  const [zZone, setZZone] = useState('');
  const [zCam, setZCam] = useState('');

  const load = useCallback(async () => {
    try {
      const [g, t, z] = await Promise.all([
        fetchCccGroups(),
        fetchCccMessageTemplates(),
        fetchCccZoneCameraMap(),
      ]);
      setGroups(g.items || []);
      setTemplates(t.items || []);
      setMappings(z.mappings || []);
    } catch {
      /* Events permission may be required */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const addGroup = async () => {
    if (!gName.trim()) return;
    try {
      await createCccGroup({ name: gName.trim(), kind: gKind });
      setGName('');
      toast.success('CCC group created (not an RBAC role)');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Group failed');
    }
  };

  const addTemplate = async () => {
    if (!tName.trim() || !tBody.trim()) return;
    try {
      await createCccMessageTemplate({ name: tName.trim(), body: tBody.trim() });
      setTName('');
      setTBody('');
      toast.success('Message template saved (internal only)');
      void load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Template failed');
    }
  };

  const addZoneMap = async () => {
    if (!zZone.trim() || !zCam.trim()) return;
    try {
      const next = [
        ...mappings.filter((m) => m.zone_key !== zZone.trim().toLowerCase()),
        {
          zone: zZone.trim(),
          zone_key: zZone.trim().toLowerCase(),
          preferred_camera_id: zCam.trim(),
          prefer_ptz: true,
        },
      ];
      const res = await saveCccZoneCameraMap(next);
      setMappings(res.mappings || next);
      setZZone('');
      setZCam('');
      toast.success('Zone → preferred camera saved');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Zone map failed');
    }
  };

  return (
    <div className="space-y-3 text-xs border-t border-gray-200 dark:border-gray-700 pt-3 mt-3" data-testid="ccc-ops-config">
      <h3 className="font-semibold text-sm">CCC ops groups &amp; message templates</h3>
      <p className="text-gray-500">
        Operational groups for assignment/recipients — separate from Users/RBAC roles. Templates are
        in-app only (no SMS/email/DMR/Tetra).
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1">
          <div className="font-medium">Groups ({groups.length})</div>
          <ul className="max-h-24 overflow-auto border rounded divide-y dark:divide-gray-700">
            {groups.map((g) => (
              <li key={g.id} className="px-2 py-1">
                {g.name} · {g.kind}
                {g.not_rbac_role ? ' · not RBAC' : ''}
              </li>
            ))}
          </ul>
          {canAdmin ? (
            <div className="flex gap-1">
              <input
                value={gName}
                onChange={(e) => setGName(e.target.value)}
                placeholder="Group name"
                className="flex-1 px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
              <select
                value={gKind}
                onChange={(e) => setGKind(e.target.value)}
                className="px-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              >
                <option value="responder">responder</option>
                <option value="rpf">rpf</option>
                <option value="ops">ops</option>
                <option value="communications">communications</option>
                <option value="custom">custom</option>
              </select>
              <button type="button" className="px-2 py-1 rounded bg-emerald-700 text-white" onClick={() => void addGroup()}>
                Add
              </button>
            </div>
          ) : (
            <span className="text-gray-500">Admin required to create groups</span>
          )}
        </div>
        <div className="space-y-1">
          <div className="font-medium">Templates ({templates.length})</div>
          <ul className="max-h-24 overflow-auto border rounded divide-y dark:divide-gray-700">
            {templates.map((t) => (
              <li key={t.id} className="px-2 py-1 truncate">
                {t.name}
                {t.external_delivery === false ? ' · internal' : ''}
              </li>
            ))}
          </ul>
          {canAdmin ? (
            <div className="space-y-1">
              <input
                value={tName}
                onChange={(e) => setTName(e.target.value)}
                placeholder="Template name"
                className="w-full px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
              <textarea
                value={tBody}
                onChange={(e) => setTBody(e.target.value)}
                placeholder="Message body"
                rows={2}
                className="w-full px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
              />
              <button type="button" className="px-2 py-1 rounded bg-emerald-700 text-white" onClick={() => void addTemplate()}>
                Add template
              </button>
            </div>
          ) : (
            <span className="text-gray-500">Admin required to create templates</span>
          )}
        </div>
      </div>

      <div className="pt-2 border-t border-gray-200 dark:border-gray-700">
        <CccVmsIntegrations />
      </div>

      <div className="space-y-1 pt-2 border-t border-gray-200 dark:border-gray-700" data-rdso="18.6.22.9">
        <div className="font-medium">Zone → preferred camera / PTZ ({mappings.length})</div>
        <p className="text-gray-500">
          Admin mapping for alarm camera response (exact/prefix zone match). No geographic distance invented.
        </p>
        <ul className="max-h-24 overflow-auto border rounded divide-y dark:divide-gray-700">
          {mappings.map((m) => (
            <li key={m.zone_key} className="px-2 py-1 truncate">
              {m.zone} → {m.preferred_camera_id}
              {m.prefer_ptz ? ' · prefer PTZ' : ''}
            </li>
          ))}
          {mappings.length === 0 ? (
            <li className="px-2 py-2 text-gray-500">No mappings</li>
          ) : null}
        </ul>
        {canAdmin ? (
          <div className="flex flex-wrap gap-1">
            <input
              value={zZone}
              onChange={(e) => setZZone(e.target.value)}
              placeholder="Zone / location path"
              className="flex-1 min-w-[8rem] px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
            <input
              value={zCam}
              onChange={(e) => setZCam(e.target.value)}
              placeholder="Preferred camera Mongo id"
              className="flex-1 min-w-[8rem] px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-transparent"
            />
            <button type="button" className="px-2 py-1 rounded bg-emerald-700 text-white" onClick={() => void addZoneMap()}>
              Save map
            </button>
          </div>
        ) : (
          <span className="text-gray-500">Admin required to edit zone map</span>
        )}
      </div>
    </div>
  );
}
