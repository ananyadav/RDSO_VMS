import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  useUrlHydration,
  useUrlSync,
  initialStringParam,
  initialEnumParam,
} from '../hooks/useUrlSearchState';
import { useVisibilityInterval } from '../hooks/useVisibilityInterval';
import toast from 'react-hot-toast';
import LocationTreePanel, { type BuildingNode, type LocationStats } from '../components/camera-management/LocationTreePanel';
import FloorSummaryCards, { type CameraListFilter } from '../components/camera-management/FloorSummaryCards';
import StatusBadge from '../components/camera-management/StatusBadge';
import { Link } from 'react-router-dom';
import {
  Plus,
  Search,
  Video,
  Edit,
  Loader2,
  List,
  Upload,
  Download,
  Eye,
  Power,
  MapPin,
  Trash2,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import AddCameraModal, { type CameraFormData, CORPORATE_CAMERA_DEFAULTS } from '../components/AddCameraModal';
import DuplicateCameraDialog, { type ExistingCameraInfo } from '../components/DuplicateCameraDialog';
import ManageLocationsModal from '../components/ManageLocationsModal';
import { useLocationsContext } from '../context/LocationsContext';
import { apiFetch, cameraQuery } from '../lib/api';
import { readSessionCache, UI_CACHE_TTL_MS, writeSessionCache } from '../lib/sessionCache';
import { authService } from '../services/authService';
import { isAdminUser } from '../lib/permissions';

interface Camera {
  id?: string;
  _id?: string;
  name: string;
  ip_address: string;
  type?: string;
  protocol?: string;
  building?: string;
  floor?: string;
  floor_group?: string;
  camera_group?: string;
  location_path?: string;
  area?: string;
  site?: string;
  is_active?: boolean;
  status?: string;
  online?: boolean;
  displayName?: string;
  camera_uid?: string;
  recordingActive?: boolean;
  lastError?: string | null;
  liveStatus?: string;
  confirmedOffline?: boolean;
  port?: number;
  model?: string;
  username?: string;
  password?: string;
  main_channel?: string;
  sub_channel?: string;
  main_rtsp_url?: string;
  sub_rtsp_url?: string;
  rtsp_url_source?: string;
  ptz?: boolean;
}

interface DuplicatePayload {
  message: string;
  existingCamera: ExistingCameraInfo;
  pendingCamera?: CameraFormData;
}

const KNOWN_PROTOCOLS = [
  'CUSTOM',
  'DAHUA',
  'HIKVISION',
  'HONEYWELL',
  'ONVIF',
  'SPARSH',
  'UNIVIEW',
  'VIVOTEK',
] as const;

/** Same grouping as Add Camera — PRAMA uses Hikvision RTSP paths. */
function protocolFilterKey(protocol: string | undefined): string {
  const p = (protocol || 'HIKVISION').trim().toUpperCase();
  if (p === 'PRAMA' || p === 'HIK') return 'HIKVISION';
  return p;
}

function protocolFilterLabel(key: string): string {
  if (key === 'HIKVISION') return 'HIKVISION / PRAMA';
  return key;
}

function initialProtocolFilter(params: { current: URLSearchParams | null }): string {
  const raw = initialStringParam(params, 'protocol');
  return raw ? protocolFilterKey(raw) : '';
}

const TOOLBAR_BTN =
  'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700/80 transition-colors disabled:opacity-50';

const TOOLBAR_BTN_PRIMARY =
  'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md border border-emerald-600/50 bg-emerald-600 text-white hover:bg-emerald-500 transition-colors disabled:opacity-50';

const ACTION_BTN =
  'inline-flex items-center gap-1 px-1.5 py-1 text-[11px] font-medium rounded hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-colors';

const GROUP_TREE_CACHE_KEY = 'cctv:mgmt:groupTree:v1';

type GroupTreeCache = {
  buildings: BuildingNode[];
  totals: LocationStats | null;
};

function scopeCamerasCacheKey(params: Record<string, string>): string {
  return `cctv:mgmt:scopeCameras:v1:${JSON.stringify(params)}`;
}

function cameraToForm(cam: Camera | null): Partial<CameraFormData> | null {
  if (!cam) return null;
  return {
    name: cam.name,
    ip_address: cam.ip_address,
    port: String(cam.port ?? 554),
    model: cam.model || 'Hikvision',
    username: cam.username || 'admin',
    password: cam.password && cam.password !== '***' ? cam.password : '',
    protocol: cam.protocol || 'HIKVISION',
    site: cam.site || CORPORATE_CAMERA_DEFAULTS.site,
    building: cam.building || CORPORATE_CAMERA_DEFAULTS.building,
    floor_group: cam.floor_group || CORPORATE_CAMERA_DEFAULTS.floor_group,
    floor: cam.floor || cam.floor_group || '6th Floor',
    area: cam.area || '',
    camera_group: cam.camera_group || '',
    location_path: cam.location_path || '',
    main_channel: cam.main_channel || '101',
    sub_channel: cam.sub_channel || '102',
    main_rtsp_url: cam.main_rtsp_url || '',
    sub_rtsp_url: cam.sub_rtsp_url || '',
    rtsp_url_source: cam.rtsp_url_source || 'auto_hikvision',
    is_active: cam.is_active !== false,
    ptz: Boolean(cam.ptz),
  };
}

function formToPayload(data: CameraFormData): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: data.name.trim(),
    ip_address: data.ip_address.trim(),
    // Port is fixed for all cameras — not shown in Add/Edit UI.
    port: 554,
    model: (data.model || data.protocol || 'HIKVISION').trim(),
    username: data.username,
    password: data.password,
    protocol: data.protocol,
    site: data.site,
    building: data.building,
    floor_group: data.floor_group,
    floor: data.floor,
    area: data.area,
    camera_group: data.camera_group,
    location_path: data.location_path,
    main_channel: data.main_channel,
    sub_channel: data.sub_channel,
    rtsp_url_source: data.rtsp_url_source,
    is_active: data.is_active,
    ptz: data.ptz,
    type: 'rtsp',
  };
  if (data.protocol === 'ONVIF' || data.protocol === 'CUSTOM') {
    payload.main_rtsp_url = data.main_rtsp_url;
    payload.sub_rtsp_url = data.sub_rtsp_url;
  }
  return payload;
}

function isCameraDisabled(cam: Camera): boolean {
  return cam.is_active === false || cam.status === 'Disabled';
}

function applyListFilter(cameras: Camera[], filter: CameraListFilter): Camera[] {
  switch (filter) {
    case 'online':
      return cameras.filter(
        (c) => !isCameraDisabled(c) && (c.online || c.liveStatus === 'online'),
      );
    case 'offline':
      return cameras.filter(
        (c) =>
          !isCameraDisabled(c) &&
          (c.liveStatus === 'offline' || c.confirmedOffline) &&
          !c.online,
      );
    case 'disabled':
      return cameras.filter((c) => isCameraDisabled(c));
    default:
      return cameras;
  }
}

function applyProtocolFilter(cameras: Camera[], protocol: string): Camera[] {
  const wanted = protocolFilterKey(protocol);
  if (!protocol.trim()) return cameras;
  return cameras.filter(
    (c) => protocolFilterKey(c.protocol) === wanted,
  );
}

function applySearchFilter(cameras: Camera[], query: string): Camera[] {
  const q = query.trim().toLowerCase();
  if (!q) return cameras;
  return cameras.filter((c) => {
    const name = (c.name || '').toLowerCase();
    const display = (c.displayName || '').toLowerCase();
    const ip = (c.ip_address || '').toLowerCase();
    return name.includes(q) || display.includes(q) || ip.includes(q);
  });
}

export default function CameraManagement() {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();

  const [scopeCameras, setScopeCameras] = useState<Camera[]>([]);
  const [discoveredCameras, setDiscoveredCameras] = useState<Camera[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [editingCamera, setEditingCamera] = useState<Camera | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [duplicate, setDuplicate] = useState<DuplicatePayload | null>(null);
  const [replaceLoading, setReplaceLoading] = useState(false);
  const [highlightId, setHighlightId] = useState<string | null>(null);

  const [protocolFilter, setProtocolFilter] = useState(() => initialProtocolFilter(initialParams));
  const [search, setSearch] = useState(() => initialStringParam(initialParams, 'q'));
  const [manageLocationsOpen, setManageLocationsOpen] = useState(false);
  const cachedGroupTree = readSessionCache<GroupTreeCache>(GROUP_TREE_CACHE_KEY, UI_CACHE_TTL_MS);
  const hadGroupTreeCacheRef = useRef(Boolean(cachedGroupTree));
  const [groupTree, setGroupTree] = useState<BuildingNode[]>(() => cachedGroupTree?.buildings ?? []);
  const [plantTotals, setPlantTotals] = useState<LocationStats | null>(() => cachedGroupTree?.totals ?? null);
  const [groupsLoading, setGroupsLoading] = useState(() => !cachedGroupTree);
  const [selectedBuilding, setSelectedBuilding] = useState<string | null>(() =>
    initialParams.current?.get('building') || null,
  );
  const [selectedGroup, setSelectedGroup] = useState<string | null>(() =>
    initialParams.current?.get('group') || null,
  );
  const [listFilter, setListFilter] = useState<CameraListFilter>(() => {
    const legacyOnline = initialEnumParam(
      initialParams,
      'online',
      ['all', 'online', 'offline'] as const,
      'all',
    );
    const filterParam = initialEnumParam(
      initialParams,
      'filter',
      ['all', 'online', 'offline', 'disabled', 'errors', 'rtc'] as const,
      'all',
    );
    const legacyStatus = initialEnumParam(
      initialParams,
      'status',
      ['all', 'active', 'disabled'] as const,
      'all',
    );
    if (filterParam === 'errors' || filterParam === 'rtc') return 'offline';
    if (filterParam !== 'all') return filterParam as CameraListFilter;
    if (legacyStatus === 'disabled') return 'disabled';
    if (legacyOnline === 'online') return 'online';
    if (legacyOnline === 'offline') return 'offline';
    return 'all';
  });
  const [mainTab, setMainTab] = useState<'cameras' | 'scan'>(() =>
    initialEnumParam(initialParams, 'tab', ['cameras', 'scan'] as const, 'cameras'),
  );
  const { sites: locationSites, buildings: locationBuildings, reload: reloadLocations } = useLocationsContext();
  const importInputRef = useRef<HTMLInputElement>(null);
  const isAdmin = isAdminUser(authService.getCurrentUser());

  const fetchGroupTree = useCallback(async (opts?: { silent?: boolean; hydrateUrl?: boolean }) => {
    const silent = opts?.silent === true;
    const hydrateUrl = opts?.hydrateUrl !== false;
    if (!silent && !hadGroupTreeCacheRef.current) setGroupsLoading(true);
    try {
      const res = await apiFetch('/api/cameras/groups?includeInactive=true&includeStats=true');
      if (!res.ok) throw new Error('Failed to load location tree');
      const data = await res.json();
      const list: BuildingNode[] = data.buildings ?? [];
      const totals = data.totals ?? null;
      setGroupTree(list);
      setPlantTotals(totals);
      writeSessionCache(GROUP_TREE_CACHE_KEY, { buildings: list, totals });
      hadGroupTreeCacheRef.current = true;
      if (hydrateUrl) {
        const urlBuilding = initialParams.current?.get('building');
        const urlGroup = initialParams.current?.get('group');
        if (urlBuilding && list.some((b) => b.building === urlBuilding)) {
          setSelectedBuilding(urlBuilding);
          if (urlGroup) {
            const node = list.find((b) => b.building === urlBuilding);
            if (node?.floorGroups.some((f) => f.camera_group === urlGroup)) {
              setSelectedGroup(urlGroup);
            }
          }
        }
        markHydrated();
      }
    } catch (err) {
      if (!silent) {
        toast.error(err instanceof Error ? err.message : 'Failed to load locations');
      }
    } finally {
      if (!silent) setGroupsLoading(false);
    }
  }, [initialParams, markHydrated]);

  useEffect(() => {
    void fetchGroupTree({ hydrateUrl: true });
  }, [fetchGroupTree]);

  const urlValues = useMemo(
    () => ({
      tab: mainTab === 'cameras' ? null : mainTab,
      building: selectedBuilding,
      group: selectedGroup,
      q: search.trim() || null,
      filter: listFilter === 'all' ? null : listFilter,
      protocol: protocolFilter || null,
    }),
    [mainTab, selectedBuilding, selectedGroup, search, listFilter, protocolFilter],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  const defaultSite = useMemo(() => {
    const sites = [...new Set(groupTree.map((b) => b.site).filter(Boolean))];
    return sites.length === 1 ? sites[0] : null;
  }, [groupTree]);

  const selectedFloorNode = useMemo(() => {
    if (!selectedBuilding || !selectedGroup) return null;
    const b = groupTree.find((x) => x.building === selectedBuilding);
    return b?.floorGroups.find((f) => f.camera_group === selectedGroup) ?? null;
  }, [groupTree, selectedBuilding, selectedGroup]);

  const buildScopeParams = useCallback((): Record<string, string> => {
    const params: Record<string, string> = { includeInactive: 'true' };
    if (selectedGroup) {
      params.camera_group = selectedGroup;
      if (selectedFloorNode?.floor) params.floor = selectedFloorNode.floor;
    }
    return params;
  }, [selectedGroup, selectedFloorNode?.floor]);

  const fetchScopeCameras = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = opts?.silent === true;
    const params = buildScopeParams();
    const cacheKey = scopeCamerasCacheKey(params);
    if (!silent) {
      const cached = readSessionCache<Camera[]>(cacheKey, UI_CACHE_TTL_MS);
      if (cached?.length) {
        setScopeCameras(cached);
      }
    }
    try {
      const response = await apiFetch(
        `/api/cameras/configured${cameraQuery(params)}`,
      );
      if (!response.ok) throw new Error('Failed to fetch configured cameras.');
      const rows = await response.json();
      setScopeCameras(rows);
      writeSessionCache(cacheKey, rows);
    } catch (err) {
      if (!silent) {
        toast.error(err instanceof Error ? err.message : 'Failed to load cameras.');
      }
    }
  }, [buildScopeParams]);

  const protocolOptions = useMemo(() => {
    const discovered = scopeCameras.map((c) => protocolFilterKey(c.protocol));
    return [...new Set([...KNOWN_PROTOCOLS, ...discovered])].sort();
  }, [scopeCameras]);

  const configuredCameras = useMemo(() => {
    const afterProtocol = applyProtocolFilter(scopeCameras, protocolFilter);
    const afterSearch = applySearchFilter(afterProtocol, search);
    return applyListFilter(afterSearch, listFilter);
  }, [scopeCameras, protocolFilter, search, listFilter]);

  useEffect(() => {
    void fetchScopeCameras();
  }, [fetchScopeCameras]);

  // Auto-refresh Last Error / online status and floor error stats while this page is open.
  useVisibilityInterval(
    () => {
      void fetchScopeCameras({ silent: true });
      void fetchGroupTree({ silent: true, hydrateUrl: false });
    },
    15000,
    true,
  );

  const handleTreeSelect = (building: string, cameraGroup: string) => {
    setSelectedBuilding(building);
    setSelectedGroup(cameraGroup);
  };

  const handleRefresh = () => {
    void fetchGroupTree({ silent: false, hydrateUrl: false });
    void fetchScopeCameras({ silent: false });
  };

  const handleExport = () => {
    if (configuredCameras.length === 0) {
      toast.error('No cameras to export for this floor');
      return;
    }
    const payload = { cameras: configuredCameras };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const slug = (selectedFloorNode?.location_path || 'cameras').replace(/[^\w]+/g, '_');
    a.href = url;
    a.download = `cameras_${slug}.json`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(`Exported ${configuredCameras.length} camera(s)`);
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as { cameras?: unknown[] } | unknown[];
      const cameras = Array.isArray(parsed) ? parsed : parsed.cameras;
      if (!Array.isArray(cameras) || cameras.length === 0) {
        throw new Error('File must contain a cameras array');
      }
      const res = await apiFetch('/api/cameras/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cameras }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Import failed');
      toast.success(`Import: ${data.created ?? 0} created, ${data.updated ?? 0} updated`);
      handleRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Import failed');
    }
  };

  const selectedLocationDefaults = useMemo((): Partial<CameraFormData> | null => {
    if (!selectedBuilding || !selectedGroup) return null;
    const b = groupTree.find((x) => x.building === selectedBuilding);
    const fg = b?.floorGroups.find((f) => f.camera_group === selectedGroup);
    if (!fg) return null;
    return {
      site: b?.site || CORPORATE_CAMERA_DEFAULTS.site,
      building: selectedBuilding,
      floor: fg.floor,
      floor_group: fg.floor_group || fg.floor,
      camera_group: fg.camera_group,
      location_path: fg.location_path,
    };
  }, [groupTree, selectedBuilding, selectedGroup]);

  const selectedFloorLabel = useMemo(() => {
    if (!selectedGroup || !selectedBuilding) return 'Select a floor';
    const b = groupTree.find((x) => x.building === selectedBuilding);
    const fg = b?.floorGroups.find((f) => f.camera_group === selectedGroup);
    return fg?.location_path ?? selectedBuilding;
  }, [groupTree, selectedBuilding, selectedGroup]);

  const summaryStats = selectedGroup ? selectedFloorNode?.stats : plantTotals;
  const summaryLabel = selectedGroup
    ? selectedFloorLabel
    : defaultSite
      ? `All cameras — ${defaultSite}`
      : 'All cameras';
  const listScopeLabel = selectedGroup ? selectedFloorLabel : summaryLabel;

  const handleApiError = async (
    response: Response,
    fallback: string,
    pendingCamera?: CameraFormData,
  ) => {
    try {
      const body = await response.json();
      const detail = body.message || body.error;
      if (response.status === 409 && body.code === 'DUPLICATE_CAMERA') {
        if (body.existingCamera) {
          setDuplicate({
            message: body.message,
            existingCamera: body.existingCamera,
            pendingCamera,
          });
        } else {
          toast.error(detail || fallback);
        }
        return true;
      }
      toast.error(detail || fallback);
      return true;
    } catch {
      toast.error(fallback);
      return true;
    }
  };

  const handleAddCamera = async (data: CameraFormData) => {
    const response = await apiFetch('/api/cameras', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(formToPayload(data)),
    });
    if (!response.ok) {
      const handled = await handleApiError(response, 'Failed to add camera', data);
      if (handled && response.status === 409) setIsAddModalOpen(false);
      return;
    }
    toast.success(`Camera added: ${data.name}`);
    setIsAddModalOpen(false);
    handleRefresh();
  };

  const handleReplaceDuplicate = async () => {
    if (!duplicate?.pendingCamera) return;
    const oldName = duplicate.existingCamera.name;
    const newName = duplicate.pendingCamera.name;
    if (!window.confirm(
      `Delete "${oldName}" and add "${newName}"?\n\nRecordings are kept by IP address.`,
    )) return;
    setReplaceLoading(true);
    try {
      const delRes = await apiFetch(`/api/cameras/${duplicate.existingCamera.id}`, { method: 'DELETE' });
      if (!delRes.ok) throw new Error('Failed to delete the existing camera');
      const addRes = await apiFetch('/api/cameras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formToPayload(duplicate.pendingCamera)),
      });
      if (!addRes.ok) {
        await handleApiError(addRes, 'Old camera deleted, but adding the new camera failed');
        return;
      }
      toast.success(`Replaced ${oldName} with ${newName}`);
      setDuplicate(null);
      handleRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Replace failed');
    } finally {
      setReplaceLoading(false);
    }
  };

  const handleSaveEditCamera = async (data: CameraFormData) => {
    if (!editingCamera?._id) return;
    const payload = formToPayload(data);
    if (!data.password) delete payload.password;
    const response = await apiFetch(`/api/cameras/${editingCamera._id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const handled = await handleApiError(response, 'Failed to update camera');
      if (handled && response.status === 409) {
        setIsEditModalOpen(false);
        setEditingCamera(null);
      }
      return;
    }
    toast.success(`Camera updated: ${data.name}`);
    setIsEditModalOpen(false);
    setEditingCamera(null);
    handleRefresh();
  };

  const openEditExisting = async (existing: ExistingCameraInfo, reactivate = false) => {
    setDuplicate(null);
    const cam = configuredCameras.find((c) => c._id === existing.id);
    if (cam) {
      setEditingCamera(cam);
      setIsEditModalOpen(true);
      if (reactivate) {
        setTimeout(() => toast('Enable "Active" and save to reactivate.', { icon: 'ℹ️' }), 300);
      }
      return;
    }
    try {
      const res = await apiFetch('/api/cameras/configured?includeInactive=true');
      const all: Camera[] = await res.json();
      const found = all.find((c) => c._id === existing.id);
      if (found) {
        setEditingCamera({ ...found, is_active: reactivate ? true : found.is_active });
        setIsEditModalOpen(true);
      }
    } catch {
      toast.error('Could not load existing camera');
    }
  };

  const handleReactivateExisting = async (existing: ExistingCameraInfo) => {
    setDuplicate(null);
    try {
      const res = await apiFetch(`/api/cameras/${existing.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: true }),
      });
      if (!res.ok) throw new Error('Reactivate failed');
      toast.success(`${existing.name} reactivated`);
      handleRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to reactivate');
    }
  };

  const handleScan = async () => {
    setIsScanning(true);
    toast('Scanning network…', { icon: '🔍' });
    try {
      const response = await apiFetch('/api/cameras/scan', { method: 'POST' });
      if (!response.ok) throw new Error('Network scan failed.');
      const data = await response.json();
      setDiscoveredCameras(data.discovered || []);
      toast.success(`Found ${(data.discovered || []).length} new camera(s)`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Scan failed');
    } finally {
      setIsScanning(false);
    }
  };

  const handleEditCamera = (camera: Camera) => {
    setEditingCamera(camera);
    setIsEditModalOpen(true);
  };

  const handleToggleActive = async (camera: Camera) => {
    if (!camera._id) return;
    const next = camera.is_active === false;
    try {
      const response = await apiFetch(`/api/cameras/${camera._id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: next }),
      });
      if (!response.ok) throw new Error('Failed to update status');
      toast.success(next ? `${camera.name} enabled` : `${camera.name} disabled`);
      handleRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update status');
    }
  };

  const handleDeleteCamera = async (camera: Camera) => {
    if (!camera._id) return;
    const label = camera.ip_address || camera.name || camera._id;
    if (
      !window.confirm(
        `Permanently delete camera "${label}"?\n\nThis cannot be undone.\nDisable keeps the camera in the list; Delete removes it from the system.\nRecordings on disk (by IP) are kept.`,
      )
    ) {
      return;
    }
    try {
      const response = await apiFetch(`/api/cameras/${camera._id}`, { method: 'DELETE' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error((body as { error?: string }).error || 'Failed to delete camera');
      }
      toast.success(`Deleted ${label}`);
      handleRefresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete camera');
    }
  };

  return (
    <div className="h-full min-h-0 flex flex-col overflow-hidden bg-gray-100 dark:bg-gray-900/40">
      <div className="shrink-0 px-3 pt-2 pb-1.5 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
        <PageHeader
          title="Camera Management"
          subtitle={mainTab === 'cameras' ? listScopeLabel : 'Network discovery utility'}
        />
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          {isAdmin && (
            <>
              <button
                type="button"
                className={TOOLBAR_BTN_PRIMARY}
                onClick={() => setIsAddModalOpen(true)}
              >
                <Plus size={14} /> Add Camera
              </button>
              <button type="button" className={TOOLBAR_BTN} onClick={() => importInputRef.current?.click()}>
                <Upload size={14} /> Import
              </button>
              <input ref={importInputRef} type="file" accept=".json,application/json" className="hidden" onChange={handleImportFile} />
            </>
          )}
          <button type="button" className={TOOLBAR_BTN} onClick={handleExport} disabled={configuredCameras.length === 0}>
            <Download size={14} /> Export
          </button>
          {isAdmin && (
            <button type="button" className={TOOLBAR_BTN} onClick={() => setManageLocationsOpen(true)}>
              <MapPin size={14} /> Manage Locations
            </button>
          )}
          <div className="flex-1" />
          <div className="flex rounded-md border border-gray-300 dark:border-gray-600 overflow-hidden">
            <button
              type="button"
              onClick={() => setMainTab('cameras')}
              className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium ${
                mainTab === 'cameras'
                  ? 'bg-white dark:bg-gray-800 text-emerald-600'
                  : 'bg-gray-100 dark:bg-gray-800/50 text-gray-500 hover:text-gray-700'
              }`}
            >
              <List size={13} /> Cameras
            </button>
            {isAdmin && (
              <button
                type="button"
                onClick={() => setMainTab('scan')}
                className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border-l border-gray-300 dark:border-gray-600 ${
                  mainTab === 'scan'
                    ? 'bg-white dark:bg-gray-800 text-emerald-600'
                    : 'bg-gray-100 dark:bg-gray-800/50 text-gray-500 hover:text-gray-700'
                }`}
              >
                <Search size={13} /> Scan
              </button>
            )}
          </div>
        </div>
      </div>

      {mainTab === 'scan' ? (
        <div className="flex-1 overflow-y-auto p-4">
          <div className="max-w-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <p className="text-xs text-gray-500 mb-3">Discover ONVIF cameras on the network, then add them from the Cameras tab.</p>
            <button onClick={handleScan} disabled={isScanning} className={TOOLBAR_BTN_PRIMARY}>
              {isScanning ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              {isScanning ? 'Scanning…' : 'Start Scan'}
            </button>
            {discoveredCameras.length > 0 && (
              <ul className="mt-4 divide-y divide-gray-200 dark:divide-gray-700 border border-gray-200 dark:border-gray-700 rounded-lg text-sm">
                {discoveredCameras.map((camera) => (
                  <li key={camera.ip_address} className="px-3 py-2 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <Video size={16} className="text-sky-400 shrink-0" />
                      <div className="min-w-0">
                        <p className="font-medium truncate">{camera.name}</p>
                        <p className="text-xs text-gray-500 font-mono">{camera.ip_address}</p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => { setMainTab('cameras'); setIsAddModalOpen(true); }}
                      className={TOOLBAR_BTN}
                    >
                      <Plus size={12} /> Add
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <aside className="w-52 shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800/80">
            <div className="shrink-0 px-2.5 py-1.5 border-b border-gray-200 dark:border-gray-700">
              <span className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Locations</span>
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto">
              <LocationTreePanel
                buildings={groupTree}
                selectedBuilding={selectedBuilding}
                selectedGroup={selectedGroup}
                onSelect={handleTreeSelect}
                loading={groupsLoading}
              />
            </div>
          </aside>

          <main className="flex-1 min-w-0 flex flex-col min-h-0 overflow-hidden">
            <div className="shrink-0 px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-white/80 dark:bg-gray-800/50 space-y-2">
              <div className="flex flex-wrap gap-2 items-center">
                <div className="flex items-center gap-2 shrink-0">
                  <input
                    type="text"
                    placeholder="Search name or IP…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="input-style py-1 px-2 text-xs w-44 min-w-[10rem]"
                  />
                  <select
                    value={protocolFilter}
                    onChange={(e) => setProtocolFilter(e.target.value)}
                    className="select-style text-xs py-1 px-2 !w-auto min-w-[11rem] max-w-[14rem]"
                  >
                    <option value="">All protocols</option>
                    {protocolOptions.map((p) => (
                      <option key={p} value={p}>{protocolFilterLabel(p)}</option>
                    ))}
                  </select>
                </div>
                <span className="text-[11px] text-gray-500 ml-auto tabular-nums">
                  {configuredCameras.length} shown
                </span>
              </div>
              {summaryStats && (
                <FloorSummaryCards
                  stats={summaryStats}
                  floorLabel={summaryLabel}
                  activeFilter={listFilter}
                  onFilterChange={setListFilter}
                />
              )}
            </div>

            <div className="flex-1 min-h-0 overflow-auto px-3 py-2">
              <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden shadow-sm">
                <table className="w-full text-xs text-left">
                  <thead className="bg-gray-50 dark:bg-gray-900/60 text-[10px] uppercase tracking-wide text-gray-500 sticky top-0 z-10 border-b border-gray-200 dark:border-gray-700">
                    <tr>
                      <th className="px-3 py-2 font-semibold">Camera</th>
                      <th className="px-3 py-2 font-semibold">IP Address</th>
                      <th className="px-3 py-2 font-semibold">Status</th>
                      <th className="px-3 py-2 font-semibold">Recording</th>
                      <th className="px-3 py-2 font-semibold min-w-[10rem]">Location</th>
                      <th className="px-3 py-2 font-semibold min-w-[8rem]">Last Error</th>
                      <th className="px-3 py-2 font-semibold text-right min-w-[14rem]">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-700/80">
                    {configuredCameras.length === 0 && (
                      <tr>
                        <td colSpan={7} className="px-4 py-10 text-center text-gray-500">
                          No cameras match the current filters.
                        </td>
                      </tr>
                    )}
                    {configuredCameras.map((camera) => {
                      const rowId = camera._id ?? camera.id ?? '';
                      const isDisabled = camera.status === 'Disabled' || camera.is_active === false;
                      const hasError = Boolean(camera.lastError) && camera.liveStatus === 'offline';
                      return (
                        <tr
                          key={rowId}
                          id={`camera-row-${rowId}`}
                          className={`hover:bg-gray-50 dark:hover:bg-gray-700/30 ${
                            highlightId === rowId ? 'bg-amber-500/10' : ''
                          }`}
                        >
                          <td className="px-3 py-1.5">
                            <div className="font-semibold text-gray-900 dark:text-white">{camera.name}</div>
                            {camera.displayName && camera.displayName !== camera.name && (
                              <div className="text-[10px] text-gray-500 truncate max-w-[10rem]">{camera.displayName}</div>
                            )}
                          </td>
                          <td className="px-3 py-1.5 font-mono text-gray-600 dark:text-gray-300">{camera.ip_address}</td>
                          <td className="px-3 py-1.5">
                            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                              {isDisabled ? (
                                <StatusBadge variant="disabled">Disabled</StatusBadge>
                              ) : (
                                <StatusBadge variant={camera.online || camera.liveStatus === 'online' ? 'online' : 'offline'}>
                                  {camera.online || camera.liveStatus === 'online' ? 'Online' : 'Offline'}
                                </StatusBadge>
                              )}
                              {hasError && camera.confirmedOffline !== false && camera.liveStatus === 'offline' && (
                                <StatusBadge variant="error">Error</StatusBadge>
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-1.5">
                            {camera.recordingActive ? (
                              <StatusBadge variant="recording">Recording</StatusBadge>
                            ) : (
                              <span className="text-gray-500">—</span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-gray-500 truncate max-w-[14rem]" title={camera.location_path || ''}>
                            {camera.location_path || `${camera.building || ''} / ${camera.floor || ''}`}
                          </td>
                          <td className="px-3 py-1.5 text-red-400/90 truncate max-w-[12rem]" title={camera.lastError || ''}>
                            {camera.lastError || '—'}
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="flex flex-wrap justify-end gap-0.5">
                              <Link to="/live" className={`${ACTION_BTN} text-sky-500`} title="View in Live View">
                                <Eye size={12} /> View
                              </Link>
                              {isAdmin && (
                                <>
                                  <button type="button" onClick={() => handleEditCamera(camera)} className={`${ACTION_BTN} text-sky-400`}>
                                    <Edit size={12} /> Edit
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleToggleActive(camera)}
                                    className={`${ACTION_BTN} ${isDisabled ? 'text-emerald-400' : 'text-amber-400'}`}
                                  >
                                    <Power size={12} />
                                    {isDisabled ? 'Reactivate' : 'Disable'}
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleDeleteCamera(camera)}
                                    className={`${ACTION_BTN} text-red-400`}
                                    title="Permanently delete this camera"
                                  >
                                    <Trash2 size={12} /> Delete
                                  </button>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </main>
        </div>
      )}

      <AddCameraModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onSave={handleAddCamera}
        locationBuildings={locationBuildings}
        locationSites={locationSites}
        defaultLocation={selectedLocationDefaults}
        onOpenManageLocations={isAdmin ? () => setManageLocationsOpen(true) : undefined}
      />
      <AddCameraModal
        isOpen={isEditModalOpen}
        onClose={() => { setIsEditModalOpen(false); setEditingCamera(null); }}
        onSave={handleSaveEditCamera}
        isEditMode
        initialData={cameraToForm(editingCamera)}
        locationBuildings={locationBuildings}
        locationSites={locationSites}
        onOpenManageLocations={isAdmin ? () => setManageLocationsOpen(true) : undefined}
      />
      <ManageLocationsModal
        isOpen={manageLocationsOpen}
        onClose={() => setManageLocationsOpen(false)}
        sites={locationSites}
        stacked={isAddModalOpen || isEditModalOpen}
        onUpdated={() => { void reloadLocations(); void fetchGroupTree(); }}
      />
      <DuplicateCameraDialog
        isOpen={Boolean(duplicate)}
        message={duplicate?.message ?? ''}
        existing={duplicate?.existingCamera ?? { id: '', name: '', ip_address: '', is_active: true }}
        pendingName={duplicate?.pendingCamera?.name}
        onClose={() => setDuplicate(null)}
        onReplace={duplicate?.pendingCamera ? handleReplaceDuplicate : undefined}
        replaceLoading={replaceLoading}
        onView={() => {
          if (!duplicate) return;
          setHighlightId(duplicate.existingCamera.id);
          setDuplicate(null);
          document.getElementById(`camera-row-${duplicate.existingCamera.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }}
        onEdit={() => duplicate && openEditExisting(duplicate.existingCamera)}
        onReactivate={duplicate && !duplicate.existingCamera.is_active
          ? () => handleReactivateExisting(duplicate.existingCamera)
          : undefined}
      />
    </div>
  );
}
