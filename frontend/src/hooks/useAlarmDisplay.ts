import { useCallback, useEffect, useRef, useState } from 'react';
import { useVisibilityInterval } from './useVisibilityInterval';
import {
  acknowledgeEvent,
  displayResetEvent,
  listUiNotifications,
  type AlarmEvent,
} from '../lib/eventsApi';
import {
  isAlarmDisplayActive,
  selectAlarmForDisplay,
  shouldOpenAlarmDisplay,
} from '../lib/alarmDisplay';
import {
  displaySwitchTargetsMonitor,
  parseAlarmDisplaySwitch,
  shouldUseLayoutSwitch,
  type AlarmDisplaySwitchConfig,
} from '../lib/alarmDisplaySwitch';
import { hasPermission, PERMISSIONS } from '../lib/permissions';
import { authService } from '../services/authService';

const POLL_MS = 8000;

export type AlarmDisplayCamera = {
  id: string;
  name: string;
  displayName?: string;
  ip_address?: string;
  cameraUid?: string;
  online: boolean;
  ptz?: boolean;
};

function resolveCamera(
  cameras: AlarmDisplayCamera[],
  event: AlarmEvent,
): AlarmDisplayCamera | null {
  const byId = cameras.find((c) => c.id === event.camera_id);
  if (byId) return byId;
  if (event.camera_uid) {
    const byUid = cameras.find(
      (c) => c.cameraUid === event.camera_uid || c.id === event.camera_uid,
    );
    if (byUid) return byUid;
  }
  return null;
}

/**
 * RDSO 18.1.25 + 18.2.28 — poll UI notifications and drive Live View alarm display.
 * Reuses caller-provided openFullscreen / closeFullscreen / layout switch (existing media path).
 */
export function useAlarmDisplay(options: {
  cameras: AlarmDisplayCamera[];
  enabled?: boolean;
  /** Logical Live View monitor identity (1–8). */
  monitorId?: number;
  openFullscreen: (camera: AlarmDisplayCamera) => void;
  closeFullscreen: () => void;
  /** RDSO 18.2.28 — apply configured layout/slot on this monitor. */
  onLayoutSwitch?: (args: {
    event: AlarmEvent;
    camera: AlarmDisplayCamera;
    config: AlarmDisplaySwitchConfig;
  }) => boolean;
  /** Restore pre-alarm layout/slots after reset/recovery when configured. */
  onLayoutRestore?: (eventId: string) => void;
}) {
  const {
    cameras,
    enabled = true,
    monitorId = 1,
    openFullscreen,
    closeFullscreen,
    onLayoutSwitch,
    onLayoutRestore,
  } = options;
  const [activeEvent, setActiveEvent] = useState<AlarmEvent | null>(null);
  const [queueCount, setQueueCount] = useState(0);
  const seenIdsRef = useRef<Set<string>>(new Set());
  const dismissedRef = useRef<Set<string>>(new Set());
  const activeIdRef = useRef<string | null>(null);
  const alarmModeRef = useRef(false);
  const layoutSwitchActiveRef = useRef(false);
  const restoreOnClearRef = useRef(true);

  const user = authService.getCurrentUser();
  const userPriority = Number(user?.priority ?? 1) || 1;
  const canPoll =
    enabled &&
    hasPermission(user, PERMISSIONS.EVENTS) &&
    hasPermission(user, PERMISSIONS.LIVE_VIEW);

  const clearAlarmSurface = useCallback(() => {
    const prevId = activeIdRef.current;
    const wasLayout = layoutSwitchActiveRef.current;
    const shouldRestore = restoreOnClearRef.current;
    activeIdRef.current = null;
    setActiveEvent(null);
    layoutSwitchActiveRef.current = false;
    if (alarmModeRef.current) {
      alarmModeRef.current = false;
      if (wasLayout) {
        if (shouldRestore && prevId && onLayoutRestore) onLayoutRestore(prevId);
      } else {
        closeFullscreen();
      }
    }
  }, [closeFullscreen, onLayoutRestore]);

  const applySelection = useCallback(
    (events: AlarmEvent[]) => {
      const activeList = events.filter(
        (e) => isAlarmDisplayActive(e) && !dismissedRef.current.has(e.id),
      );
      setQueueCount(activeList.length);

      // Current display recovered / reset elsewhere → auto-reset surface (18.1.25.2)
      if (activeIdRef.current) {
        const current = events.find((e) => e.id === activeIdRef.current);
        if (!current || !isAlarmDisplayActive(current) || dismissedRef.current.has(current.id)) {
          clearAlarmSurface();
        }
      }

      const selected = selectAlarmForDisplay(events, {
        currentEventId: activeIdRef.current,
        locallyDismissedIds: dismissedRef.current,
        userPriority,
      });

      if (!selected) {
        if (alarmModeRef.current) clearAlarmSurface();
        return;
      }

      setActiveEvent(selected);
      const cam = resolveCamera(cameras, selected);
      // Unauthorized / not in this Live View ACL-filtered set → do not display
      if (!cam) return;

      const switchCfg = parseAlarmDisplaySwitch(selected);
      if (switchCfg && !displaySwitchTargetsMonitor(switchCfg, monitorId)) {
        // Configured for another monitor — leave this window alone
        return;
      }

      if (shouldOpenAlarmDisplay(selected, seenIdsRef.current)) {
        seenIdsRef.current.add(selected.id);
        activeIdRef.current = selected.id;
        alarmModeRef.current = true;
        restoreOnClearRef.current = switchCfg?.restoreOnReset !== false;

        if (shouldUseLayoutSwitch(switchCfg) && onLayoutSwitch && switchCfg) {
          const applied = onLayoutSwitch({ event: selected, camera: cam, config: switchCfg });
          layoutSwitchActiveRef.current = Boolean(applied);
          if (!applied) {
            // Fall back to fullscreen if layout apply failed (e.g. ACL)
            layoutSwitchActiveRef.current = false;
            openFullscreen(cam);
          } else {
            closeFullscreen();
          }
        } else {
          layoutSwitchActiveRef.current = false;
          openFullscreen(cam);
        }
      } else if (activeIdRef.current === selected.id) {
        setActiveEvent(selected);
      }
    },
    [
      cameras,
      clearAlarmSurface,
      closeFullscreen,
      monitorId,
      onLayoutSwitch,
      openFullscreen,
      userPriority,
    ],
  );

  const poll = useCallback(async () => {
    if (!canPoll) return;
    try {
      const page = await listUiNotifications({ acknowledged: false, limit: 40 });
      applySelection(page.items || []);
    } catch {
      // Permission or network — Live View remains usable
    }
  }, [applySelection, canPoll]);

  useVisibilityInterval(poll, canPoll ? POLL_MS : null);

  useEffect(() => {
    if (canPoll) void poll();
  }, [canPoll, poll, cameras.length]);

  const manualReset = useCallback(async () => {
    const ev = activeEvent;
    if (!ev) {
      clearAlarmSurface();
      return;
    }
    dismissedRef.current.add(ev.id);
    try {
      await displayResetEvent(ev.id);
    } catch {
      // Local dismiss still applies
    }
    clearAlarmSurface();
    void poll();
  }, [activeEvent, clearAlarmSurface, poll]);

  const acknowledgeActive = useCallback(async () => {
    const ev = activeEvent;
    if (!ev) return;
    dismissedRef.current.add(ev.id);
    try {
      await acknowledgeEvent(ev.id);
    } catch {
      /* ignore */
    }
    clearAlarmSurface();
    void poll();
  }, [activeEvent, clearAlarmSurface, poll]);

  /** Closing alarmed fullscreen = manual reset of alarmed video (not acknowledge). */
  const onOperatorCloseFullscreen = useCallback(() => {
    if (alarmModeRef.current && activeIdRef.current) {
      void manualReset();
      return;
    }
    closeFullscreen();
  }, [closeFullscreen, manualReset]);

  return {
    activeEvent,
    queueCount,
    alarmDisplayActive: Boolean(activeEvent && alarmModeRef.current),
    alarmLayoutSwitchActive: Boolean(activeEvent && layoutSwitchActiveRef.current),
    manualReset,
    acknowledgeActive,
    onOperatorCloseFullscreen,
  };
}
