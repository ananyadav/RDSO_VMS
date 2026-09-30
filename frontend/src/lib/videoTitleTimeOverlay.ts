/** RDSO 18.2.29 — GUI title + date/time overlay helpers (APP_TIMEZONE). */

import {
  formatInAppTimezone,
  getCachedAppTimezone,
} from './appTimezone';

export function formatVideoOverlayDate(
  now: Date = new Date(),
  timeZone: string = getCachedAppTimezone(),
): string {
  return formatInAppTimezone(
    now,
    {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    },
    timeZone,
  );
}

export function formatVideoOverlayTime(
  now: Date = new Date(),
  timeZone: string = getCachedAppTimezone(),
): string {
  return formatInAppTimezone(
    now,
    {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    },
    timeZone,
  );
}

export function formatVideoOverlayDateTime(
  now: Date = new Date(),
  timeZone: string = getCachedAppTimezone(),
): { date: string; time: string; line: string } {
  const date = formatVideoOverlayDate(now, timeZone);
  const time = formatVideoOverlayTime(now, timeZone);
  return { date, time, line: `${date} ${time}` };
}

export function videoTitleTimeOverlayCapabilityPublic(): Record<string, unknown> {
  return {
    rdso_18_2_29: true,
    client_side_overlay: true,
    burns_into_stream: false,
    uses_app_timezone: true,
    live_tiles: true,
    fullscreen: true,
    continuous_clock: true,
  };
}
