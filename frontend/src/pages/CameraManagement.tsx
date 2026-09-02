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
import VirtualizedCameraTableBody, {
  scrollManagementTableToCamera,
} from '../components/camera-management/VirtualizedCameraTableBody';
import {
  Plus,
  Search,
  Loader2,
  List,
  Upload,
  Download,
  MapPin,
  X,
} from 'lucide-react';
import PageHeader from '../components/PageHeader';
import AddCameraModal, { type CameraFormData, CORPORATE_CAMERA_DEFAULTS } from '../components/AddCameraModal';
import DuplicateCameraDialog, { type ExistingCameraInfo } from '../components/DuplicateCameraDialog';
import ManageLocationsModal from '../components/ManageLocationsModal';
import StreamProfileModal from '../components/StreamProfileModal';
import { useLocationsContext } from '../context/LocationsContext';
import { apiFetch, cameraQuery } from '../lib/api';
import { go2rtcStreamName } from '../lib/liveProvider';
import { go2rtcFrameJpegSrc } from '../lib/mediaUrls';
import { readSessionCache, UI_CACHE_TTL_MS, writeSessionCache } from '../lib/sessionCache';
import { authService } from '../services/authService';
import { isOpsAdminUser } from '../lib/permissions';

interface DiscoveredCamera {
  ip_address: string;
  name?: string;
  manufacturer?: string;
  model?: string;
  onvif_endpoint?: string;
  status: 'new' | 'already_added';
}

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
  cameraUid?: string;
  camera_uid?: string;
  workerId?: number | string | null;
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
  recording_channel?: string;
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

function cameraRowId(camera: Camera): string {
  return String(camera._id ?? camera.id ?? '');
}

function snapshotFrameSrc(camera: Camera): string | null {
  const uid = (camera.cameraUid || camera.camera_uid || '').trim();
  if (!uid) return null;
  return `${go2rtcFrameJpegSrc(go2rtcStreamName(uid, 'sub'), camera.workerId)}&t=${Date.now()}`;
}

const GROUP_TREE_CACHE_KEY = 'cctv:mgmt:groupTree:v1';

type GroupTreeCache = {
  buildings: BuildingNode[];
  totals: LocationStats | null;
};

function scopeCamerasCacheKey(params: Record<string, string>): string {
  return `cctv:mgmt:scopeCameras:v1:${JSON.stringify(params)}`;
}

function recordingChannelToFormValue(
  recording_channel?: string,
  main_channel?: string,
  sub_channel?: string,
): 'main' | 'sub' {
  const raw = (recording_channel || '').trim().toLowerCase();
  if (raw === 'main' || raw === 'sub') return raw;
  const mainCh = main_channel || '101';
  const subCh = sub_channel || '102';
  if (raw === mainCh) return 'main';
  if (raw === subCh) return 'sub';
  return 'main';
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
    recording_channel: recordingChannelToFormValue(
      cam.recording_channel,
      cam.main_channel,
      cam.sub_channel,
    ),
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
    recording_channel: data.recording_channel,
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
  const [discoveredCameras, setDiscoveredCameras] = useState<DiscoveredCamera[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [addFromDiscovery, setAddFromDiscovery] = useState<Partial<CameraFormData> | null>(null);
  const [discoverySubnets, setDiscoverySubnets] = useState<string[]>([]);
  const [selectedDiscoverySubnet, setSelectedDiscoverySubnet] = useState('');
  const [customDiscoveryCidr, setCustomDiscoveryCidr] = useState('');
  const [lastScanMeta, setLastScanMeta] = useState<{ subnet?: string | null; durationMs?: number } | null>(null);
  const scanAbortRef = useRef<AbortController | null>(null);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [editingCamera, setEditingCamera] = useState<Camera | null>(null);
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [duplicate, setDuplicate] = useState<DuplicatePayload | null>(null);
  const [replaceLoading, setReplaceLoading] = useState(false);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<{ camera: Camera; src: string } | null>(null);
  const [streamProfileCamera, setStreamProfileCamera] = useState<Camera | null>(null);

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
  const [mainTab, setMainTab] = useState<'cameras' | 'discover'>(() =>
    initialEnumParam(initialParams, 'tab', ['cameras', 'discover', 'scan'] as const, 'cameras') === 'scan'
      ? 'discover'
      : initialEnumParam(initialParams, 'tab', ['cameras', 'discover'] as const, 'cameras'),
  );
  const { sites: locationSites, buildings: locationBuildings, reload: reloadLocations } = useLocationsContext();
  const importInputRef = useRef<HTMLInputElement>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const isAdmin = isOpsAdminUser(authService.getCurrentUser());

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

  useEffect(() => {
    if (!isAdmin || mainTab !== 'discover') return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/cameras/discovery/subnets');
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const list: string[] = data.subnets || [];
        if (!cancelled) {
          setDiscoverySubnets(list);
          if (!selectedDiscoverySubnet && list.length > 0) {
            const preferred = list.find((s) => s.startsWith('192.168.41.')) || list[0];
            setSelectedDiscoverySubnet(preferred);
          }
        }
      } catch {
        /* ignore — admin can enter CIDR manually */
      }
    })();
    return () => { cancelled = true; };
  }, [isAdmin, mainTab, selectedDiscoverySubnet]);

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

  const mgmtTableScopeKey = useMemo(
    () =>
      `${selectedGroup ?? ''}:${protocolFilter}:${search}:${listFilter}:${configuredCameras.length}:${configuredCameras[0]?._id ?? ''}`,
    [selectedGroup, protocolFilter, search, listFilter, configuredCameras],
  );

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
    setAddFromDiscovery(null);
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

  const handleCancelScan = () => {
    scanAbortRef.current?.abort();
    scanAbortRef.current = null;
    setIsScanning(false);
    toast('Discovery cancelled', { icon: '⏹️' });
  };

  const handleScan = async () => {
    const subnet = customDiscoveryCidr.trim() || selectedDiscoverySubnet.trim();
    scanAbortRef.current?.abort();
    const controller = new AbortController();
    scanAbortRef.current = controller;
    setIsScanning(true);
    const started = performance.now();
    toast(subnet ? `Discovering on ${subnet}…` : 'Running ONVIF WS-Discovery…', { icon: '🔍' });
    try {
      const response = await apiFetch('/api/cameras/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(subnet ? { subnet } : {}),
        signal: controller.signal,
      });
      if (response.status === 403) throw new Error('Admin access required.');
      if (response.status === 401) throw new Error('Please sign in again.');
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((data as { error?: string }).error || 'Camera discovery failed.');
      }
      const rows: DiscoveredCamera[] = (data.discovered || []).map((row: DiscoveredCamera) => ({
        ip_address: row.ip_address || '',
        name: row.name || '',
        manufacturer: row.manufacturer || '',
        model: row.model || '',
        onvif_endpoint: row.onvif_endpoint || '',
        status: row.status === 'already_added' ? 'already_added' : 'new',
      }));
      setDiscoveredCameras(rows);
      setLastScanMeta({
        subnet: data.subnet_scanned || subnet || null,
        durationMs: Math.round(performance.now() - started),
      });
      const newCount = rows.filter((r) => r.status === 'new').length;
      const addedCount = rows.filter((r) => r.status === 'already_added').length;
      toast.success(`Found ${rows.length} device(s): ${newCount} new, ${addedCount} already added`);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      toast.error(err instanceof Error ? err.message : 'Discovery failed');
    } finally {
      if (scanAbortRef.current === controller) scanAbortRef.current = null;
      setIsScanning(false);
    }
  };

  const handleAddDiscovered = (camera: DiscoveredCamera) => {
    if (camera.status === 'already_added') return;
    setAddFromDiscovery({
      name: camera.name?.trim() || `ONVIF ${camera.ip_address}`,
      ip_address: camera.ip_address,
      protocol: 'ONVIF',
      model: camera.model?.trim() || '',
      port: '554',
      username: 'admin',
      password: '',
    });
    setIsAddModalOpen(true);
  };

  const handleEditCamera = (camera: Camera) => {
    setEditingCamera(camera);
    setIsEditModalOpen(true);
  };

  const handleOpenSnapshot = (camera: Camera) => {
    const src = snapshotFrameSrc(camera);
    if (!src) {
      toast.error('No stream id for this camera');
      return;
    }
    setSnapshot({ camera, src });
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
          subtitle={mainTab === 'cameras' ? listScopeLabel : 'ONVIF WS-Discovery + routed subnet scan'}
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
            <>
              <button
                type="button"
                className={TOOLBAR_BTN}
                onClick={() => { setMainTab('discover'); void handleScan(); }}
                disabled={isScanning}
              >
                {isScanning ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                Discover Cameras
              </button>
              <button type="button" className={TOOLBAR_BTN} onClick={() => setManageLocationsOpen(true)}>
                <MapPin size={14} /> Manage Locations
              </button>
            </>
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
                onClick={() => setMainTab('discover')}
                className={`inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium border-l border-gray-300 dark:border-gray-600 ${
                  mainTab === 'discover'
                    ? 'bg-white dark:bg-gray-800 text-emerald-600'
                    : 'bg-gray-100 dark:bg-gray-800/50 text-gray-500 hover:text-gray-700'
                }`}
              >
                <Search size={13} /> Discover
              </button>
            )}
          </div>
        </div>
      </div>

      {mainTab === 'discover' ? (
        <div className="flex-1 overflow-y-auto p-4">
          <div className="max-w-5xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <p className="text-xs text-gray-500 mb-3">
              Runs ONVIF WS-Discovery on the local VLAN, then optionally scans one selected routed subnet for RTSP (port 554).
              Cameras are not added automatically — choose which device to register.
            </p>
            <div className="flex flex-wrap items-end gap-2 mb-3">
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-gray-500">Camera subnet</span>
                <select
                  value={selectedDiscoverySubnet}
                  onChange={(e) => setSelectedDiscoverySubnet(e.target.value)}
                  disabled={isScanning}
                  className="select-style text-xs py-1 px-2 min-w-[12rem]"
                >
                  <option value="">WS-Discovery only</option>
                  {discoverySubnets.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-gray-500">Or enter CIDR (/24 max)</span>
                <input
                  type="text"
                  placeholder="192.168.41.0/24"
                  value={customDiscoveryCidr}
                  onChange={(e) => setCustomDiscoveryCidr(e.target.value)}
                  disabled={isScanning}
                  className="input-style py-1 px-2 text-xs w-44 font-mono"
                />
              </label>
              <button onClick={handleScan} disabled={isScanning} className={TOOLBAR_BTN_PRIMARY}>
                {isScanning ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                {isScanning ? 'Discovering…' : 'Discover Cameras'}
              </button>
              {isScanning && (
                <button type="button" onClick={handleCancelScan} className={TOOLBAR_BTN}>
                  Cancel
                </button>
              )}
            </div>
            {lastScanMeta?.subnet && (
              <p className="text-[11px] text-gray-500 mb-2">
                Last subnet scan: {lastScanMeta.subnet}
                {lastScanMeta.durationMs != null ? ` · ${(lastScanMeta.durationMs / 1000).toFixed(1)}s` : ''}
              </p>
            )}
            {discoveredCameras.length > 0 && (
              <div className="mt-4 overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-lg">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 dark:bg-gray-900/50 text-left text-[11px] uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-3 py-2 font-semibold">IP address</th>
                      <th className="px-3 py-2 font-semibold">Manufacturer</th>
                      <th className="px-3 py-2 font-semibold">Model</th>
                      <th className="px-3 py-2 font-semibold">ONVIF endpoint</th>
                      <th className="px-3 py-2 font-semibold">Status</th>
                      <th className="px-3 py-2 font-semibold text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {discoveredCameras.map((camera) => (
                      <tr key={camera.ip_address}>
                        <td className="px-3 py-2 font-mono text-xs">{camera.ip_address}</td>
                        <td className="px-3 py-2">{camera.manufacturer || '—'}</td>
                        <td className="px-3 py-2">{camera.model || camera.name || '—'}</td>
                        <td className="px-3 py-2 font-mono text-[11px] max-w-xs truncate" title={camera.onvif_endpoint}>
                          {camera.onvif_endpoint || '—'}
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className={`inline-flex px-2 py-0.5 rounded text-[11px] font-medium ${
                              camera.status === 'already_added'
                                ? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                                : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                            }`}
                          >
                            {camera.status === 'already_added' ? 'Already Added' : 'New'}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-right">
                          {camera.status === 'new' ? (
                            <button
                              type="button"
                              onClick={() => handleAddDiscovered(camera)}
                              className={TOOLBAR_BTN}
                            >
                              <Plus size={12} /> Add
                            </button>
                          ) : (
                            <span className="text-xs text-gray-400">In system</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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

            <div ref={tableScrollRef} className="flex-1 min-h-0 overflow-auto px-3 py-2">
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
                      <th className="px-3 py-2 font-semibold text-right min-w-[18rem]">Actions</th>
                    </tr>
                  </thead>
                  <VirtualizedCameraTableBody
                    cameras={configuredCameras}
                    highlightId={highlightId}
                    isAdmin={isAdmin}
                    scrollContainerRef={tableScrollRef}
                    scopeKey={mgmtTableScopeKey}
                    onEdit={handleEditCamera}
                    onOpenSnapshot={handleOpenSnapshot}
                    onStreamProfile={setStreamProfileCamera}
                    onToggleActive={handleToggleActive}
                    onDelete={handleDeleteCamera}
                  />
                </table>
              </div>
            </div>
          </main>
        </div>
      )}

      <AddCameraModal
        isOpen={isAddModalOpen}
        onClose={() => { setIsAddModalOpen(false); setAddFromDiscovery(null); }}
        onSave={handleAddCamera}
        initialData={addFromDiscovery}
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
          const id = duplicate.existingCamera.id;
          setHighlightId(id);
          setDuplicate(null);
          scrollManagementTableToCamera(tableScrollRef.current, configuredCameras, id);
          requestAnimationFrame(() => {
            document.getElementById(`camera-row-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          });
        }}
        onEdit={() => duplicate && openEditExisting(duplicate.existingCamera)}
        onReactivate={duplicate && !duplicate.existingCamera.is_active
          ? () => handleReactivateExisting(duplicate.existingCamera)
          : undefined}
      />
      {snapshot && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setSnapshot(null)}
        >
          <div
            className="w-full max-w-3xl rounded-lg border border-gray-700 bg-gray-900 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2 border-b border-gray-700 px-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-white">{snapshot.camera.name}</p>
                <p className="truncate font-mono text-[11px] text-gray-400">{snapshot.camera.ip_address}</p>
              </div>
              <button
                type="button"
                className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-white"
                onClick={() => setSnapshot(null)}
                title="Close"
              >
                <X size={16} />
              </button>
            </div>
            <div className="bg-black">
              <img
                src={snapshot.src}
                alt={`Snapshot ${snapshot.camera.name}`}
                className="mx-auto max-h-[70vh] w-full object-contain"
                onError={() => toast.error('Could not load snapshot')}
              />
            </div>
            <div className="flex justify-end gap-2 border-t border-gray-700 px-3 py-2">
              <a
                href={snapshot.src}
                download={`${(snapshot.camera.name || 'camera').replace(/\s+/g, '_')}_snapshot.jpg`}
                className={`${TOOLBAR_BTN} no-underline`}
              >
                Download
              </a>
              <button type="button" className={TOOLBAR_BTN} onClick={() => setSnapshot(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
      {streamProfileCamera?._id && (
        <StreamProfileModal
          cameraId={streamProfileCamera._id}
          cameraName={streamProfileCamera.name}
          onClose={() => setStreamProfileCamera(null)}
        />
      )}
    </div>
  );
}
