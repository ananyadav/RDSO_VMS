/** Multi-brand RTSP URL builder (mirrors backend rtsp_utils.build_rtsp_urls). */

export type RtspBrand =
  | 'HIKVISION'
  | 'PRAMA'
  | 'DAHUA'
  | 'UNIVIEW'
  | 'UNV'
  | 'VIVOTEK'
  | 'HONEYWELL'
  | 'SPARSH';

export interface BuildRtspUrlsInput {
  make: string;
  ip: string;
  username: string;
  password: string;
  port?: number | string;
}

export interface BuildRtspUrlsResult {
  protocol: string;
  main_channel: string;
  sub_channel: string;
  recording_channel: string;
  main_rtsp_url: string;
  sub_rtsp_url: string;
  rtsp_source: string;
  fallback_urls: string[];
}

interface RtspBrandSpec {
  main_path: string;
  sub_path: string;
  main_channel: string;
  sub_channel: string;
  rtsp_source: string;
  fallback_paths: string[];
}

const BRAND_ALIASES: Record<string, RtspBrand> = {
  HIK: 'HIKVISION',
  UNV: 'UNIVIEW',
};

function normalizeMake(make: string): RtspBrand | 'ONVIF' | 'CUSTOM' {
  const brand = (make || 'HIKVISION').trim().toUpperCase();
  const mapped = (BRAND_ALIASES[brand] ?? brand) as RtspBrand | 'ONVIF' | 'CUSTOM';
  return mapped;
}

function encodeAuth(username: string, password: string): { user: string; pass: string } {
  return {
    user: encodeURIComponent((username || 'admin').trim()),
    pass: encodeURIComponent(String(password ?? '').trim()),
  };
}

function assembleUrl(
  scheme: 'rtsp' | 'onvif',
  ip: string,
  port: number,
  path: string,
  username: string,
  password: string,
): string {
  const host = (ip || '').trim();
  if (!host) return '';
  const { user, pass } = encodeAuth(username, password);
  if (scheme === 'onvif') return `onvif://${user}:${pass}@${host}`;
  const suffix = port ? `:${port}` : '';
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `rtsp://${user}:${pass}@${host}${suffix}${normalizedPath}`;
}

function brandSpec(make: RtspBrand): RtspBrandSpec {
  switch (make) {
    case 'PRAMA':
    case 'HIKVISION':
      return {
        main_path: '/Streaming/Channels/101',
        sub_path: '/Streaming/Channels/102',
        main_channel: '101',
        sub_channel: '102',
        rtsp_source: make === 'PRAMA' ? 'auto_prama' : 'auto_hikvision',
        fallback_paths: [] as string[],
      };
    case 'DAHUA':
      return {
        main_path: '/cam/realmonitor?channel=1&subtype=0',
        sub_path: '/cam/realmonitor?channel=1&subtype=1',
        main_channel: '1',
        sub_channel: '1',
        rtsp_source: 'auto_dahua',
        fallback_paths: [],
      };
    case 'UNIVIEW':
      return {
        main_path: '/media/video1',
        sub_path: '/media/video2',
        main_channel: '1',
        sub_channel: '2',
        rtsp_source: 'auto_uniview',
        fallback_paths: ['/media/video3'],
      };
    case 'VIVOTEK':
      return {
        main_path: '/live1s1.sdp',
        sub_path: '/live1s2.sdp',
        main_channel: '1',
        sub_channel: '2',
        rtsp_source: 'auto_vivotek',
        fallback_paths: ['/live.sdp'],
      };
    case 'HONEYWELL':
      return {
        main_path: '/rtsp/streaming?channel=01&subtype=A',
        sub_path: '/rtsp/streaming?channel=01&subtype=B',
        main_channel: '01',
        sub_channel: '01',
        rtsp_source: 'auto_honeywell',
        fallback_paths: [
          '/rtsp/streaming?channel=01&subtype=0',
          '/rtsp/streaming?channel=01&subtype=1',
          '/h264',
          '/cam1/h264',
          '/PSIA/Streaming/channels/1',
        ],
      };
    case 'SPARSH':
      return {
        main_path: '/ch01.264?ptype=tcp&dev=1',
        sub_path: '/ch01_sub.264?ptype=tcp&dev=1',
        main_channel: 'ch01',
        sub_channel: 'ch01_sub',
        rtsp_source: 'auto_sparsh',
        fallback_paths: [],
      };
    default:
      return brandSpec('HIKVISION');
  }
}

export function buildRtspUrls(input: BuildRtspUrlsInput): BuildRtspUrlsResult {
  const make = normalizeMake(input.make);
  const port = Number(input.port || 554) || 554;
  const brand = make === 'UNV' ? 'UNIVIEW' : (make as RtspBrand);
  const spec = brandSpec(brand);

  if (brand === 'SPARSH') {
    const fallback_urls = [
      assembleUrl('onvif', input.ip, port, '', input.username, input.password),
      ...(['DAHUA', 'UNIVIEW'] as const).flatMap((tpl) => {
        const b = buildRtspUrls({ ...input, make: tpl });
        return [b.main_rtsp_url, b.sub_rtsp_url];
      }),
    ].filter((url, i, arr) => url && arr.indexOf(url) === i);
    return {
      protocol: brand,
      main_channel: spec.main_channel,
      sub_channel: spec.sub_channel,
      recording_channel: spec.sub_channel,
      main_rtsp_url: assembleUrl(
        'rtsp',
        input.ip,
        port,
        spec.main_path,
        input.username,
        input.password,
      ),
      sub_rtsp_url: assembleUrl(
        'rtsp',
        input.ip,
        port,
        spec.sub_path,
        input.username,
        input.password,
      ),
      rtsp_source: 'auto_sparsh',
      fallback_urls,
    };
  }

  const main_rtsp_url = assembleUrl(
    'rtsp',
    input.ip,
    port,
    spec.main_path,
    input.username,
    input.password,
  );
  const sub_rtsp_url = assembleUrl(
    'rtsp',
    input.ip,
    port,
    spec.sub_path,
    input.username,
    input.password,
  );
  const fallback_urls = (spec.fallback_paths || [])
    .map((path) => assembleUrl('rtsp', input.ip, port, path, input.username, input.password))
    .filter((url, idx, arr) => url && arr.indexOf(url) === idx);

  return {
    protocol: brand,
    main_channel: spec.main_channel,
    sub_channel: spec.sub_channel,
    recording_channel: spec.sub_channel,
    main_rtsp_url,
    sub_rtsp_url,
    rtsp_source: spec.rtsp_source,
    fallback_urls,
  };
}

export const AUTO_RTSP_BRANDS: RtspBrand[] = [
  'HIKVISION',
  'PRAMA',
  'DAHUA',
  'UNIVIEW',
  'VIVOTEK',
  'HONEYWELL',
  'SPARSH',
];

export function isAutoRtspBrand(protocol: string): boolean {
  const p = normalizeMake(protocol);
  return AUTO_RTSP_BRANDS.includes(p as RtspBrand);
}
