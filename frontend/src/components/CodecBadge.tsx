import React from 'react';
import type { TileCodecBadge } from '../lib/hlsCodecCompat';

interface CodecBadgeProps {
  badge: TileCodecBadge;
}

export function CodecBadge({ badge }: CodecBadgeProps) {
  if (badge === 'none') return null;

  if (badge === 'h264') {
    return (
      <span className="flex-shrink-0 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-900/85 text-emerald-100 border border-emerald-700/50">
        H.264 supported
      </span>
    );
  }

  if (badge === 'h265-warning') {
    return (
      <span className="flex-shrink-0 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-amber-900/90 text-amber-100 border border-amber-600/50">
        H.265 may not play in browser
      </span>
    );
  }

  return (
    <span className="flex-shrink-0 px-2 py-0.5 text-[10px] font-semibold rounded-full bg-red-900/90 text-red-100 border border-red-600/50">
      Stream error
    </span>
  );
}
