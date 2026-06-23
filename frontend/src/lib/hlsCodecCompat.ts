import Hls, { type ErrorData } from 'hls.js';

export const CODEC_UNSUPPORTED_MESSAGE =
  'This camera stream codec is not supported by this browser. Configure substream 102 as H.264 or use Edge/Safari.';

export type TileCodecBadge = 'none' | 'h264' | 'h265-warning' | 'error';

const CODEC_ERROR_DETAILS = new Set<string>([
  Hls.ErrorDetails.MANIFEST_INCOMPATIBLE_CODECS_ERROR,
  Hls.ErrorDetails.BUFFER_ADD_CODEC_ERROR,
  Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR,
]);

const HEVC_PATTERN = /hvc1|hev1|h265|hevc|h\.265/i;
const AVC_PATTERN = /avc1|avc3|h264|h\.264/i;

export function browserSupportsHevcInMse(): boolean {
  if (typeof MediaSource === 'undefined') return false;
  return (
    MediaSource.isTypeSupported('video/mp4; codecs="hvc1.1.6.L93.B0"') ||
    MediaSource.isTypeSupported('video/mp4; codecs="hev1.1.6.L93.B0"')
  );
}

export function isHevcCodec(codec: string | undefined | null): boolean {
  if (!codec) return false;
  return HEVC_PATTERN.test(codec);
}

export function isAvcCodec(codec: string | undefined | null): boolean {
  if (!codec) return false;
  return AVC_PATTERN.test(codec);
}

export function codecFromHlsLevels(hls: Hls): string {
  const level = hls.levels[hls.currentLevel] ?? hls.levels[0];
  if (!level) return '';
  return level.videoCodec || level.attrs?.CODECS || level.codecSet || '';
}

export function codecFromPlaylistText(text: string): string {
  const match = text.match(/CODECS="([^"]+)"/i);
  return match?.[1] ?? '';
}

export function isCodecRelatedHlsError(data: ErrorData): boolean {
  if (data.details && CODEC_ERROR_DETAILS.has(data.details)) {
    return true;
  }
  const blob = [
    data.details,
    data.reason,
    data.error?.message,
    data.err?.message,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return /codec|hevc|h265|h\.265|hev1|hvc1|not supported|addsourcebuffer|incompatible|mime type/.test(
    blob,
  );
}

export function tileBadgeForCodec(
  codec: string,
  options?: { hasError?: boolean },
): TileCodecBadge {
  if (options?.hasError) return 'error';
  if (isHevcCodec(codec) && !browserSupportsHevcInMse()) {
    return 'h265-warning';
  }
  if (isAvcCodec(codec) || (isHevcCodec(codec) && browserSupportsHevcInMse())) {
    return 'h264';
  }
  return 'none';
}

export function shouldWarnHevcInBrowser(codec: string): boolean {
  return isHevcCodec(codec) && !browserSupportsHevcInMse();
}
