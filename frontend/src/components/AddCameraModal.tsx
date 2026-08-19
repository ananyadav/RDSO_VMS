import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X, Camera, ChevronDown, ChevronUp, MapPin, Eye, EyeOff } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  buildRtspUrls,
  isAutoRtspBrand,
} from '../lib/rtspUrls';
import {
  DEFAULT_SITE_NAME,
  activeLocationSites,
  buildingsForSite,
  buildingsForSiteTree,
  floorsForBuildingTree,
  locationForBuildingFloor,
  siteNamesFromTree,
  zonesForBuilding,
  type LocationBuilding,
  type LocationSite,
} from '../constants/corporateFloors';

export interface CameraFormData {
  name: string;
  ip_address: string;
  port: string;
  model: string;
  username: string;
  password: string;
  protocol: string;
  site: string;
  building: string;
  floor_group: string;
  floor: string;
  area: string;
  camera_group: string;
  location_path: string;
  main_channel: string;
  sub_channel: string;
  main_rtsp_url: string;
  sub_rtsp_url: string;
  rtsp_url_source: string;
  is_active: boolean;
  ptz: boolean;
}

export const CORPORATE_CAMERA_DEFAULTS: CameraFormData = {
  name: '',
  ip_address: '',
  port: '554',
  model: '',
  username: 'admin',
  password: '',
  protocol: 'HIKVISION',
  site: '',
  building: '',
  floor_group: '',
  floor: '',
  area: '',
  camera_group: '',
  location_path: '',
  main_channel: '101',
  sub_channel: '102',
  main_rtsp_url: '',
  sub_rtsp_url: '',
  rtsp_url_source: 'auto_hikvision',
  is_active: true,
  ptz: false,
};

function defaultsForLocations(
  sites: LocationSite[],
  buildings: LocationBuilding[],
): CameraFormData {
  const active = activeLocationSites(sites);
  if (active.length > 0) {
    const site = active[0];
    const building = site.buildings[0];
    const floor = building?.floors[0]?.name || '';
    if (site.name && building?.name && floor) {
      const loc = locationForBuildingFloor(site.name, building.name, floor);
      return {
        ...CORPORATE_CAMERA_DEFAULTS,
        site: loc.site,
        building: loc.building,
        floor: loc.floor,
        floor_group: loc.floor_group,
        camera_group: loc.camera_group,
        location_path: loc.location_path,
      };
    }
  }
  return defaultsForBuildings(buildings);
}

function defaultsForBuildings(buildings: LocationBuilding[]): CameraFormData {
  const first = buildings[0];
  if (!first) return { ...CORPORATE_CAMERA_DEFAULTS };
  const floor = first.floors[0] || 'Ground Floor';
  const loc = locationForBuildingFloor(first.site, first.building, floor);
  return {
    ...CORPORATE_CAMERA_DEFAULTS,
    site: loc.site,
    building: loc.building,
    floor: loc.floor,
    floor_group: loc.floor_group,
    camera_group: loc.camera_group,
    location_path: loc.location_path,
  };
}

function applyLocationFields(
  next: CameraFormData,
  site: string,
  building: string,
  zone: string,
): CameraFormData {
  const trimmed = zone.trim();
  const loc = trimmed
    ? locationForBuildingFloor(site, building, trimmed, '')
    : {
        site,
        building,
        floor: '',
        floor_group: '',
        area: '',
        camera_group: '',
        location_path: building ? `${site} / ${building}` : site,
      };
  return {
    ...next,
    site: loc.site,
    building: loc.building,
    floor: loc.floor,
    floor_group: loc.floor_group,
    area: '',
    camera_group: loc.camera_group,
    location_path: loc.location_path,
  };
}

interface AddCameraModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: CameraFormData) => void | Promise<void>;
  initialData?: Partial<CameraFormData> | null;
  isEditMode?: boolean;
  locationBuildings?: LocationBuilding[];
  locationSites?: LocationSite[];
  /** Pre-fill building/floor/group when adding from Camera Management selection */
  defaultLocation?: Partial<CameraFormData> | null;
  /** Open Manage Locations (admin only) without leaving this form */
  onOpenManageLocations?: () => void;
}

export default function AddCameraModal({
  isOpen,
  onClose,
  onSave,
  initialData,
  isEditMode = false,
  locationBuildings = [],
  locationSites = [],
  defaultLocation = null,
  onOpenManageLocations,
}: AddCameraModalProps) {
  const [form, setForm] = useState<CameraFormData>(CORPORATE_CAMERA_DEFAULTS);
  const [showStream, setShowStream] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const wasOpenRef = useRef(false);

  const isManualRtsp = form.protocol === 'ONVIF' || form.protocol === 'CUSTOM';
  const isAutoRtsp = isAutoRtspBrand(form.protocol);

  const applyAutoRtspFields = (
    base: CameraFormData,
    opts?: { previousIp?: string },
  ): CameraFormData => {
    if (!isAutoRtspBrand(base.protocol) || !base.ip_address.trim()) return base;
    const built = buildRtspUrls({
      make: base.protocol,
      ip: base.ip_address.trim(),
      username: base.username,
      password: base.password,
      port: 554,
    });
    const ip = base.ip_address.trim();
    const prevIp = (opts?.previousIp || '').trim();
    const currentName = base.name.trim();
    const nameStillDefault = !currentName || currentName === prevIp;
    return {
      ...base,
      port: '554',
      name: nameStillDefault ? ip : currentName,
      main_channel: built.main_channel,
      sub_channel: built.sub_channel,
      main_rtsp_url: built.main_rtsp_url,
      sub_rtsp_url: built.sub_rtsp_url,
      rtsp_url_source: built.rtsp_source,
    };
  };

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      if (isEditMode) {
        const base = defaultsForLocations(locationSites, locationBuildings);
        setForm({ ...base, ...initialData });
      } else {
        // Add mode: start blank; optionally prefill if a tree location is already selected.
        const blank = { ...CORPORATE_CAMERA_DEFAULTS };
        setForm(defaultLocation ? { ...blank, ...defaultLocation } : blank);
      }
      setShowStream(initialData?.protocol === 'ONVIF' || initialData?.protocol === 'CUSTOM');
      setShowPassword(false);
    }
    wasOpenRef.current = isOpen;
  }, [isOpen, initialData, locationBuildings, locationSites, defaultLocation, isEditMode]);

  useEffect(() => {
    if (!isOpen || isEditMode) return;
    setForm((prev) => {
      if (!prev.site || !prev.building) return prev;
      const zones = locationSites.length > 0
        ? floorsForBuildingTree(locationSites, prev.site, prev.building)
        : zonesForBuilding(locationBuildings, prev.building, prev.site);
      if (zones.length === 0) return prev;
      if (!prev.floor || zones.includes(prev.floor)) return prev;
      return applyLocationFields(prev, prev.site, prev.building, zones[0]);
    });
  }, [locationSites, locationBuildings, isOpen, isEditMode]);

  const locationDerived = useMemo(
    () => locationForBuildingFloor(form.site, form.building, form.floor.trim(), ''),
    [form.site, form.building, form.floor],
  );

  const hasSiteTree = locationSites.length > 0;

  const siteOptions = useMemo(() => {
    if (hasSiteTree) return siteNamesFromTree(locationSites);
    const names = new Set<string>();
    for (const b of locationBuildings) names.add(b.site);
    if (names.size === 0) names.add(DEFAULT_SITE_NAME);
    return Array.from(names);
  }, [hasSiteTree, locationSites, locationBuildings]);

  const buildingOptions = useMemo(() => {
    const configured = hasSiteTree
      ? buildingsForSiteTree(locationSites, form.site).map((b) => ({
        id: b.id,
        building: b.name,
      }))
      : buildingsForSite(locationBuildings, form.site);
    if (
      isEditMode &&
      form.building.trim() &&
      !configured.some((item) => item.building === form.building)
    ) {
      return [
        ...configured,
        { id: `stored:${form.site}:${form.building}`, building: form.building },
      ];
    }
    return configured;
  }, [
    hasSiteTree,
    locationSites,
    locationBuildings,
    form.site,
    form.building,
    isEditMode,
  ]);

  const zoneOptions = useMemo(() => {
    const configured = hasSiteTree
      ? floorsForBuildingTree(locationSites, form.site, form.building)
      : zonesForBuilding(locationBuildings, form.building, form.site);
    if (
      isEditMode &&
      form.floor.trim() &&
      !configured.includes(form.floor)
    ) {
      return [...configured, form.floor];
    }
    return configured;
  }, [
    hasSiteTree,
    locationSites,
    locationBuildings,
    form.building,
    form.site,
    form.floor,
    isEditMode,
  ]);

  const noFloorsConfigured = buildingOptions.length > 0 && zoneOptions.length === 0;

  if (!isOpen) return null;

  const pickZoneForBuilding = (
    site: string,
    building: string,
    currentZone: string,
  ): string => {
    const zones = hasSiteTree
      ? floorsForBuildingTree(locationSites, site, building)
      : zonesForBuilding(locationBuildings, building, site);
    if (zones.length === 0) return '';
    if (zones.includes(currentZone)) return currentZone;
    return zones[0];
  };

  const setField = <K extends keyof CameraFormData>(key: K, value: CameraFormData[K]) => {
    setForm((prev) => {
      let next = { ...prev, [key]: value };
      if (key === 'site') {
        let building = next.building;
        if (hasSiteTree) {
          const siteBuildings = buildingsForSiteTree(locationSites, String(value));
          building = siteBuildings[0]?.name ?? building;
        } else {
          const siteBuildings = buildingsForSite(locationBuildings, String(value));
          building = siteBuildings[0]?.building ?? building;
        }
        const zone = pickZoneForBuilding(String(value), building, next.floor);
        next = applyLocationFields(next, String(value), building, zone);
      }
      if (key === 'building') {
        const site = next.site;
        const zone = pickZoneForBuilding(site, String(value), next.floor);
        next = applyLocationFields(next, site, String(value), zone);
      }
      if (key === 'floor') {
        next = applyLocationFields(next, next.site, next.building, String(value));
      }
      if (key === 'protocol') {
        const p = String(value);
        if (isAutoRtspBrand(p)) {
          next.rtsp_url_source = buildRtspUrls({
            make: p,
            ip: next.ip_address,
            username: next.username,
            password: next.password,
            port: next.port,
          }).rtsp_source;
        } else if (p === 'ONVIF') {
          next.rtsp_url_source = 'onvif';
        } else {
          next.rtsp_url_source = 'custom';
        }
      }
      if (
        key === 'ip_address' ||
        key === 'port' ||
        key === 'username' ||
        key === 'password' ||
        key === 'protocol'
      ) {
        next = applyAutoRtspFields(next, { previousIp: prev.ip_address });
      }
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.site.trim()) {
      toast.error('Select a site / unit');
      return;
    }
    if (!form.building.trim()) {
      toast.error('Select a building / area');
      return;
    }
    if (!form.floor.trim()) {
      toast.error('Select a floor / zone / sub-area');
      return;
    }
    if (noFloorsConfigured) {
      toast.error('Add a floor for this building in Manage Locations first');
      return;
    }
    setSaving(true);
    try {
      const loc = locationForBuildingFloor(form.site, form.building, form.floor.trim(), '');
      await onSave({
        ...form,
        ...loc,
        port: '554',
        model: form.protocol,
        area: '',
        floor_group: loc.floor_group,
      });
    } finally {
      setSaving(false);
    }
  };

  const inputClass = 'input-style w-full';
  const labelClass = 'block text-sm font-medium text-gray-300 mb-1';

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <form onSubmit={handleSubmit} className="flex flex-col min-h-0 flex-1">
          <div className="flex items-center justify-between p-4 border-b border-gray-700 flex-shrink-0">
            <h3 className="text-xl font-bold text-white flex items-center">
              <Camera size={20} className="mr-3" />
              {isEditMode ? 'Edit Camera' : 'Add New Camera'}
            </h3>
            <button type="button" onClick={onClose} className="text-gray-400 hover:text-white">
              <X size={24} />
            </button>
          </div>

          <div className="p-6 space-y-5 overflow-y-auto flex-1">
            <section>
              <h4 className="text-sm font-semibold text-blue-300 mb-3 uppercase tracking-wide">Basic Details</h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className={labelClass}>Camera Name *</label>
                  <input className={inputClass} value={form.name} onChange={(e) => setField('name', e.target.value)} required placeholder="Cam24" />
                </div>
                <div>
                  <label className={labelClass}>IP Address *</label>
                  <input className={inputClass} value={form.ip_address} onChange={(e) => setField('ip_address', e.target.value)} required placeholder="192.168.41.50" />
                </div>
                <div>
                  <label className={labelClass}>Protocol *</label>
                  <select
                    className={inputClass}
                    value={form.protocol === 'PRAMA' ? 'HIKVISION' : form.protocol}
                    onChange={(e) => setField('protocol', e.target.value)}
                  >
                    <option value="HIKVISION">HIKVISION / PRAMA</option>
                    <option value="DAHUA">DAHUA</option>
                    <option value="UNIVIEW">UNIVIEW</option>
                    <option value="VIVOTEK">VIVOTEK</option>
                    <option value="HONEYWELL">HONEYWELL</option>
                    <option value="SPARSH">SPARSH</option>
                    <option value="ONVIF">ONVIF</option>
                    <option value="CUSTOM">CUSTOM</option>
                  </select>
                </div>
                <div>
                  <label className={labelClass}>Username *</label>
                  <input className={inputClass} value={form.username} onChange={(e) => setField('username', e.target.value)} required />
                </div>
                <div>
                  <label className={labelClass}>Password *</label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      className={`${inputClass} pr-10`}
                      value={form.password}
                      onChange={(e) => setField('password', e.target.value)}
                      required={!isEditMode}
                      autoComplete="new-password"
                      placeholder={isEditMode ? 'Leave blank to keep current' : 'Enter camera password'}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-200"
                      aria-label={showPassword ? 'Hide password' : 'Show password'}
                      tabIndex={-1}
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section>
              <div className="flex items-center justify-between gap-2 mb-3">
                <h4 className="text-sm font-semibold text-emerald-300 uppercase tracking-wide">Location</h4>
                {onOpenManageLocations && (
                  <button
                    type="button"
                    onClick={onOpenManageLocations}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400 hover:text-emerald-300"
                  >
                    <MapPin size={14} />
                    Add New Location
                  </button>
                )}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className={labelClass}>Site / Unit *</label>
                  {siteOptions.length > 0 ? (
                    <select
                      className={inputClass}
                      value={form.site}
                      onChange={(e) => setField('site', e.target.value)}
                      required
                    >
                      <option value="">Select site / unit…</option>
                      {siteOptions.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  ) : (
                    <input className={inputClass} value={form.site} onChange={(e) => setField('site', e.target.value)} required />
                  )}
                </div>
                <div>
                  <label className={labelClass}>Building / Department / Area *</label>
                  {siteOptions.length > 0 ? (
                    <select
                      className={inputClass}
                      value={form.building}
                      onChange={(e) => setField('building', e.target.value)}
                      required
                      disabled={!form.site}
                    >
                      <option value="">Select building / area…</option>
                      {buildingOptions.map((b) => (
                        <option key={b.id} value={b.building}>{b.building}</option>
                      ))}
                    </select>
                  ) : (
                    <input className={inputClass} value={form.building} onChange={(e) => setField('building', e.target.value)} required />
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className={labelClass}>Floor / Zone / Sub-area *</label>
                  {!form.site || !form.building ? (
                    <p className="text-sm text-gray-400 bg-gray-700/40 border border-gray-600 rounded-md px-3 py-2">
                      Select a site and building first.
                    </p>
                  ) : noFloorsConfigured ? (
                    <p className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-md px-3 py-2">
                      No floors configured for this building.{' '}
                      {onOpenManageLocations ? (
                        <button
                          type="button"
                          onClick={onOpenManageLocations}
                          className="font-medium underline hover:text-amber-200"
                        >
                          Add one in Manage Locations
                        </button>
                      ) : (
                        <span className="font-medium">Add one in Manage Locations</span>
                      )}{' '}
                      first.
                    </p>
                  ) : zoneOptions.length === 0 ? (
                    <p className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-md px-3 py-2">
                      No locations configured yet.{' '}
                      {onOpenManageLocations && (
                        <button
                          type="button"
                          onClick={onOpenManageLocations}
                          className="font-medium underline hover:text-amber-200"
                        >
                          Add New Location
                        </button>
                      )}
                    </p>
                  ) : (
                    <select
                      className={inputClass}
                      value={form.floor}
                      onChange={(e) => setField('floor', e.target.value)}
                      required
                    >
                      <option value="">Select floor / zone…</option>
                      {zoneOptions.map((z) => (
                        <option key={z} value={z}>{z}</option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className={labelClass}>Location path</label>
                  <input className={inputClass} value={locationDerived.location_path} readOnly />
                </div>
                <div className="sm:col-span-2">
                  <label className={labelClass}>Camera group (auto)</label>
                  <input
                    className={`${inputClass} font-mono text-gray-400`}
                    value={locationDerived.camera_group}
                    readOnly
                    tabIndex={-1}
                  />
                </div>
              </div>
            </section>

            <section>
              <button
                type="button"
                className="flex items-center gap-2 text-sm font-semibold text-sky-300 mb-3 uppercase tracking-wide"
                onClick={() => setShowStream((v) => !v)}
              >
                Stream Details {showStream ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              </button>
              {showStream && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div>
                    <label className={labelClass}>Main Channel</label>
                    <input className={inputClass} value={form.main_channel} onChange={(e) => setField('main_channel', e.target.value)} disabled={isAutoRtsp} />
                  </div>
                  <div>
                    <label className={labelClass}>Sub Channel</label>
                    <input className={inputClass} value={form.sub_channel} onChange={(e) => setField('sub_channel', e.target.value)} disabled={isAutoRtsp} />
                  </div>
                  {isManualRtsp ? (
                    <>
                      <div className="sm:col-span-3">
                        <label className={labelClass}>Main RTSP URL</label>
                        <input className={inputClass} value={form.main_rtsp_url} onChange={(e) => setField('main_rtsp_url', e.target.value)} />
                      </div>
                      <div className="sm:col-span-3">
                        <label className={labelClass}>Sub RTSP URL</label>
                        <input className={inputClass} value={form.sub_rtsp_url} onChange={(e) => setField('sub_rtsp_url', e.target.value)} />
                      </div>
                    </>
                  ) : (
                    <p className="sm:col-span-3 text-xs text-gray-500">
                      RTSP URLs are generated from IP, credentials, and brand template (sub for grid, main for fullscreen).
                    </p>
                  )}
                  <div className="sm:col-span-3">
                    <label className={labelClass}>RTSP URL Source</label>
                    <input className={inputClass} value={form.rtsp_url_source} readOnly />
                  </div>
                </div>
              )}
            </section>

            <section>
              <h4 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wide">Status</h4>
              <label className="flex items-center gap-2 text-sm text-gray-300 mb-2">
                <input type="checkbox" className="checkbox-style" checked={form.is_active} onChange={(e) => setField('is_active', e.target.checked)} />
                Active (show in Live View and go2rtc)
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" className="checkbox-style" checked={form.ptz} onChange={(e) => setField('ptz', e.target.checked)} />
                PTZ camera (pan/tilt/zoom — Hikvision, Dahua, ONVIF, and other brands)
              </label>
            </section>
          </div>

          <div className="flex items-center justify-end p-4 border-t border-gray-700 space-x-2 flex-shrink-0">
            <button type="button" onClick={onClose} className="btn-secondary px-4 py-2 text-sm">Cancel</button>
            <button
              type="submit"
              disabled={saving || noFloorsConfigured || zoneOptions.length === 0}
              className="btn-primary px-4 py-2 text-sm disabled:opacity-50"
            >
              {saving ? 'Saving…' : isEditMode ? 'Update Camera' : 'Add Camera'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
