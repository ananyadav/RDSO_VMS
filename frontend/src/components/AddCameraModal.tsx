import React, { useEffect, useMemo, useState } from 'react';
import { X, Camera, ChevronDown, ChevronUp } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  CORPORATE_OFFICE,
  DEFAULT_SITE_NAME,
  buildingsForSite,
  buildingDefFor,
  locationForBuildingFloor,
  zonesForBuilding,
  type LocationBuilding,
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
  preview_channel: string;
  main_rtsp_url: string;
  sub_rtsp_url: string;
  preview_rtsp_url: string;
  rtsp_url_source: string;
  is_active: boolean;
  ptz: boolean;
}

export const CORPORATE_CAMERA_DEFAULTS: CameraFormData = {
  name: '',
  ip_address: '',
  port: '554',
  model: 'Hikvision',
  username: 'admin',
  password: 'Corp#2024',
  protocol: 'HIKVISION',
  site: DEFAULT_SITE_NAME,
  building: CORPORATE_OFFICE,
  floor_group: 'Ground Floor',
  floor: 'Ground Floor',
  area: '',
  camera_group: 'rml_6_corporate_office_ground_floor',
  location_path: 'RML - 6 / Corporate Office / Ground Floor',
  main_channel: '101',
  sub_channel: '102',
  preview_channel: '103',
  main_rtsp_url: '',
  sub_rtsp_url: '',
  preview_rtsp_url: '',
  rtsp_url_source: 'auto_hikvision',
  is_active: true,
  ptz: false,
};

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

interface AddCameraModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: CameraFormData) => void | Promise<void>;
  initialData?: Partial<CameraFormData> | null;
  isEditMode?: boolean;
  locationBuildings?: LocationBuilding[];
  /** Pre-fill building/floor/group when adding from Camera Management selection */
  defaultLocation?: Partial<CameraFormData> | null;
}

export default function AddCameraModal({
  isOpen,
  onClose,
  onSave,
  initialData,
  isEditMode = false,
  locationBuildings = [],
  defaultLocation = null,
}: AddCameraModalProps) {
  const [form, setForm] = useState<CameraFormData>(CORPORATE_CAMERA_DEFAULTS);
  const [showStream, setShowStream] = useState(false);
  const [saving, setSaving] = useState(false);

  const isManualRtsp = form.protocol === 'ONVIF' || form.protocol === 'CUSTOM';

  useEffect(() => {
    if (isOpen) {
      const base = defaultsForBuildings(locationBuildings);
      const withLocation = !isEditMode && defaultLocation
        ? { ...base, ...defaultLocation }
        : base;
      setForm({ ...withLocation, ...initialData });
      setShowStream(initialData?.protocol === 'ONVIF' || initialData?.protocol === 'CUSTOM');
    }
  }, [isOpen, initialData, locationBuildings, defaultLocation, isEditMode]);

  const locationDerived = useMemo(
    () => locationForBuildingFloor(form.site, form.building, form.floor.trim(), ''),
    [form.site, form.building, form.floor],
  );

  const siteOptions = useMemo(() => {
    const names = new Set<string>();
    for (const b of locationBuildings) names.add(b.site);
    if (names.size === 0) names.add(DEFAULT_SITE_NAME);
    return Array.from(names);
  }, [locationBuildings]);

  const buildingOptions = useMemo(
    () => buildingsForSite(locationBuildings, form.site),
    [locationBuildings, form.site],
  );

  const zoneOptions = useMemo(
    () => zonesForBuilding(locationBuildings, form.building, form.site),
    [locationBuildings, form.building, form.site],
  );

  const useFreeTextZone = zoneOptions.length === 0;

  if (!isOpen) return null;

  const applyLocationFields = (
    next: CameraFormData,
    site: string,
    building: string,
    zone: string,
  ): CameraFormData => {
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
  };

  const pickZoneForBuilding = (
    site: string,
    building: string,
    currentZone: string,
  ): string => {
    const zones = zonesForBuilding(locationBuildings, building, site);
    if (zones.length === 0) return '';
    if (zones.includes(currentZone)) return currentZone;
    return zones[0];
  };

  const setField = <K extends keyof CameraFormData>(key: K, value: CameraFormData[K]) => {
    setForm((prev) => {
      let next = { ...prev, [key]: value };
      if (key === 'site') {
        const siteBuildings = buildingsForSite(locationBuildings, String(value));
        const building = siteBuildings[0]?.building ?? next.building;
        const zone = pickZoneForBuilding(String(value), building, next.floor);
        next = applyLocationFields(next, String(value), building, zone);
      }
      if (key === 'building') {
        const bdef = buildingDefFor(locationBuildings, String(value), next.site);
        const site = bdef?.site ?? next.site;
        const zone = pickZoneForBuilding(site, String(value), next.floor);
        next = applyLocationFields(next, site, String(value), zone);
      }
      if (key === 'floor') {
        next = applyLocationFields(next, next.site, next.building, String(value));
      }
      if (key === 'protocol') {
        const p = String(value);
        if (p === 'HIKVISION') {
          next.rtsp_url_source = 'auto_hikvision';
        } else if (p === 'ONVIF') {
          next.rtsp_url_source = 'onvif';
        } else {
          next.rtsp_url_source = 'custom';
        }
      }
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.floor.trim()) {
      toast.error('Enter a floor, zone, or area for this building');
      return;
    }
    setSaving(true);
    try {
      const loc = locationForBuildingFloor(form.site, form.building, form.floor.trim(), '');
      await onSave({
        ...form,
        ...loc,
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
                  <label className={labelClass}>Port</label>
                  <input type="number" className={inputClass} value={form.port} onChange={(e) => setField('port', e.target.value)} min={1} max={65535} />
                </div>
                <div>
                  <label className={labelClass}>Model</label>
                  <input className={inputClass} value={form.model} onChange={(e) => setField('model', e.target.value)} placeholder="Hikvision" />
                </div>
                <div>
                  <label className={labelClass}>Protocol *</label>
                  <select className={inputClass} value={form.protocol} onChange={(e) => setField('protocol', e.target.value)}>
                    <option value="HIKVISION">HIKVISION</option>
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
                  <input
                    type="password"
                    className={inputClass}
                    value={form.password}
                    onChange={(e) => setField('password', e.target.value)}
                    required={!isEditMode}
                    placeholder={isEditMode ? 'Leave blank to keep current' : ''}
                  />
                </div>
              </div>
            </section>

            <section>
              <h4 className="text-sm font-semibold text-emerald-300 mb-3 uppercase tracking-wide">Location</h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className={labelClass}>Site / Unit *</label>
                  {locationBuildings.length > 0 ? (
                    <select
                      className={inputClass}
                      value={form.site}
                      onChange={(e) => setField('site', e.target.value)}
                      required
                    >
                      {siteOptions.map((s) => (
                        <option key={s} value={s}>{s}</option>
                      ))}
                    </select>
                  ) : (
                    <input className={inputClass} value={form.site} onChange={(e) => setField('site', e.target.value)} required />
                  )}
                </div>
                <div>
                  <label className={labelClass}>Building *</label>
                  {locationBuildings.length > 0 ? (
                    <select
                      className={inputClass}
                      value={form.building}
                      onChange={(e) => setField('building', e.target.value)}
                      required
                    >
                      {buildingOptions.map((b) => (
                        <option key={b.id} value={b.building}>{b.building}</option>
                      ))}
                    </select>
                  ) : (
                    <input className={inputClass} value={form.building} onChange={(e) => setField('building', e.target.value)} required />
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className={labelClass}>
                    Floor / Zone / Area *
                  </label>
                  {useFreeTextZone ? (
                    <>
                      <input
                        className={inputClass}
                        value={form.floor}
                        onChange={(e) => setField('floor', e.target.value)}
                        placeholder="e.g. Ground Floor, Reception, Main Parking Lot"
                        required
                      />
                      <p className="mt-1 text-xs text-gray-500">
                        This building has no pre-configured zones — enter a floor name or area label.
                      </p>
                    </>
                  ) : (
                    <select
                      className={inputClass}
                      value={form.floor}
                      onChange={(e) => setField('floor', e.target.value)}
                      required
                    >
                      {zoneOptions.map((z) => (
                        <option key={z} value={z}>{z}</option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className={labelClass}>Location</label>
                  <input className={inputClass} value={locationDerived.location_path} readOnly />
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
                    <input className={inputClass} value={form.main_channel} onChange={(e) => setField('main_channel', e.target.value)} disabled={!isManualRtsp && form.protocol === 'HIKVISION'} />
                  </div>
                  <div>
                    <label className={labelClass}>Sub Channel</label>
                    <input className={inputClass} value={form.sub_channel} onChange={(e) => setField('sub_channel', e.target.value)} disabled={!isManualRtsp && form.protocol === 'HIKVISION'} />
                  </div>
                  <div>
                    <label className={labelClass}>Preview Channel</label>
                    <input className={inputClass} value={form.preview_channel} onChange={(e) => setField('preview_channel', e.target.value)} disabled={!isManualRtsp && form.protocol === 'HIKVISION'} />
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
                      <div className="sm:col-span-3">
                        <label className={labelClass}>Preview RTSP URL</label>
                        <input className={inputClass} value={form.preview_rtsp_url} onChange={(e) => setField('preview_rtsp_url', e.target.value)} />
                      </div>
                    </>
                  ) : (
                    <p className="sm:col-span-3 text-xs text-gray-500">
                      RTSP URLs are generated automatically from IP, credentials, and Hikvision channels (101/102/103).
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
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input type="checkbox" className="checkbox-style" checked={form.is_active} onChange={(e) => setField('is_active', e.target.checked)} />
                Active (show in Live View and go2rtc)
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-300 mt-2">
                <input type="checkbox" className="checkbox-style" checked={form.ptz} onChange={(e) => setField('ptz', e.target.checked)} />
                PTZ capable
              </label>
            </section>
          </div>

          <div className="flex items-center justify-end p-4 border-t border-gray-700 space-x-2 flex-shrink-0">
            <button type="button" onClick={onClose} className="btn-secondary px-4 py-2 text-sm">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary px-4 py-2 text-sm disabled:opacity-50">
              {saving ? 'Saving…' : isEditMode ? 'Update Camera' : 'Add Camera'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
