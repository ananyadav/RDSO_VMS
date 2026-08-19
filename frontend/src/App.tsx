import React, { useState, useEffect, useCallback, useRef } from 'react';
import { BrowserRouter as Router, Route, Switch, Redirect } from "react-router-dom";
import toast, { Toaster } from 'react-hot-toast';

// --- Hooks ---
import { useTheme } from './hooks/useTheme';

// --- Component & Layout Imports ---
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import { renderProtected, renderHome, renderControlCenter, renderSuperAdminOnly } from "./components/ProtectedRoute";
import { isAdminUser, isOpsAdminUser, isSuperAdminUser, PERMISSIONS, firstAllowedPath } from './lib/permissions';

// --- Page Imports ---
import LoginPage from "./pages/LoginPage";
import LiveView from "./pages/LiveView";
import Playback from "./pages/Playback";
import Events from "./pages/Events";
import PTZ from "./pages/PTZ";
import CameraManagement from "./pages/CameraManagement";
import Storage from "./pages/Storage";
import NetworkSettings from "./pages/NetworkSettings";
import UserManagement from "./pages/UserManagement";
import Notifications from "./pages/Notifications";
import SystemStatus from "./pages/SystemStatus";
import Go2RtcDiagnostics from "./pages/Go2RtcDiagnostics";
import Maintenance from "./pages/Maintenance";
import ControlCenter from "./pages/ControlCenter";
import type { User } from './services/authService';
import { authService } from './services/authService';
import { apiFetch } from './lib/api';
import { LocationsProvider } from './context/LocationsContext';
import { LiveControlRoomProvider, useLiveControlRoom } from './context/LiveControlRoomContext';
import ErrorBoundary from './components/ErrorBoundary';
import { useVisibilityInterval } from './hooks/useVisibilityInterval';

type RecordingScheduleType = Record<string, boolean>;

export default function App(): React.ReactElement {
  // --- State Management ---
  const [currentUser, setCurrentUser] = useState<User | null>(() => authService.getCurrentUser());
  const [sessionReady, setSessionReady] = useState(() => Boolean(authService.getCurrentUser()));
  const stopSessionSyncRef = useRef<(() => void) | null>(null);

  const beginSessionSync = useCallback(() => {
    stopSessionSyncRef.current?.();
    stopSessionSyncRef.current = authService.startSessionSync((user) => {
      setCurrentUser(user);
      if (!user) {
        toast.error('Your session ended. Please log in again.');
      }
    });
  }, []);

  const endSessionSync = useCallback(() => {
    stopSessionSyncRef.current?.();
    stopSessionSyncRef.current = null;
  }, []);
  const [recordingSchedule, setRecordingSchedule] = useState<RecordingScheduleType>({});
  const [isRecordingEnabled, setIsRecordingEnabled] = useState(false);

  // --- Theme Hook ---
  const [theme, toggleTheme] = useTheme();

  // --- Restore and validate session from localStorage ---
  useEffect(() => {
    const initSession = async () => {
      const hadCachedUser = Boolean(authService.getCurrentUser());
      if (hadCachedUser) {
        setSessionReady(true);
        beginSessionSync();
      }
      try {
        const fresh = await authService.refreshSession();
        if (fresh) {
          setCurrentUser(fresh);
          if (!hadCachedUser) {
            beginSessionSync();
          }
        } else if (hadCachedUser) {
          setCurrentUser(null);
        }
      } catch (err) {
        console.warn('[auth] initSession failed:', err);
        if (!hadCachedUser) {
          setCurrentUser(null);
        }
      } finally {
        setSessionReady(true);
      }
    };

    void initSession();
    return () => endSessionSync();
  }, [beginSessionSync, endSessionSync]);

  // --- Data Fetching ---
  const userId = currentUser?.id;
  useEffect(() => {
    const fetchSchedule = async () => {
      try {
        const response = await apiFetch('/api/recordings/schedule');
        const data = await response.json();
        setRecordingSchedule(data.schedule);
        setIsRecordingEnabled(data.master_enabled);
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      } catch (_error) {
        console.error("Failed to fetch recording schedule", _error);
      }
    };
    if (userId) { void fetchSchedule(); }
  }, [userId]);

  // Keep stream-health cache warm while any project tab is open so Camera Management
  // / diagnostics error columns stay current without requiring a manual refresh.
  useVisibilityInterval(
    () => {
      void apiFetch('/api/go2rtc/health-scan', { cache: 'no-store' }).catch(() => {});
    },
    30000,
    Boolean(currentUser && isOpsAdminUser(currentUser)),
  );

  // --- Handlers (useCallback keeps references stable across re-renders so
  //     child components don't think their props changed) ---
  const handleToggleCameraRecording = useCallback(async (cameraId: string) => {
    try {
      const response = await apiFetch(`/api/recordings/${cameraId}/toggle`, { method: 'POST' });
      const data = await response.json();
      setRecordingSchedule(prev => ({ ...prev, [data.id]: data.recording }));
      const schedRes = await apiFetch('/api/recordings/schedule');
      const sched = await schedRes.json();
      setIsRecordingEnabled(Boolean(sched.master_enabled));
      toast.success(`Recording for camera ${data.id} is now ${data.recording ? 'ON' : 'OFF'}`);
    } catch (_error) { toast.error("Failed to update recording status."); }
  }, []);

  const handleScheduleUpdate = useCallback(async (
    newSchedule: RecordingScheduleType,
    options?: { quiet?: boolean },
  ) => {
    try {
      const response = await apiFetch('/api/recordings/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ schedule: newSchedule }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Failed to save schedule');
      const saved = (data.schedule ?? newSchedule) as RecordingScheduleType;
      setRecordingSchedule(saved);
      setIsRecordingEnabled(Boolean(data.master_enabled));
      if (!options?.quiet) {
        toast.success(
          data.master_enabled
            ? 'Schedule saved — recording active for selected cameras'
            : 'Schedule saved — turn on recording when ready',
        );
      }
    } catch (_error) { toast.error("Failed to save schedule."); }
  }, []);

  const handleSetMasterRecording = useCallback(async (isEnabled: boolean) => {
    const previous = isRecordingEnabled;
    setIsRecordingEnabled(isEnabled);
    try {
      const response = await apiFetch('/api/recordings/master', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: isEnabled }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Failed to update master status');
      setIsRecordingEnabled(Boolean(data.master_enabled));
      toast.success(`Recording is now ${data.master_enabled ? 'ON' : 'OFF'}`);
      if (data.master_enabled) {
        const schedRes = await apiFetch('/api/recordings/schedule');
        if (schedRes.ok) {
          const sched = await schedRes.json();
          setRecordingSchedule(sched.schedule ?? {});
        }
      }
    } catch (err) {
      setIsRecordingEnabled(previous);
      toast.error(err instanceof Error ? err.message : 'Failed to update master status');
    }
  }, [isRecordingEnabled]);

  const handleLoginSuccess = useCallback((user: User) => {
    setCurrentUser(user);
    beginSessionSync();
  }, [beginSessionSync]);

  const handleLogout = () => {
    endSessionSync();
    void authService.logout().then(() => {
      setCurrentUser(null);
      toast('You have been logged out.');
    });
  };

  // --- Render Logic ---
  if (!sessionReady) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-300">
        Checking session…
      </div>
    );
  }

  if (!currentUser) {
    return (
      <>
        <LoginPage onLoginSuccess={handleLoginSuccess} />
        <Toaster position="top-right" toastOptions={{ style: { background: '#333', color: '#fff' } }}/>
      </>
    );
  }

  const accessProfileKey = JSON.stringify({
    cameraAccess: currentUser.cameraAccess ?? {},
    permissions: currentUser.permissions ?? [],
  });

  const liveView = (
    <LiveView
      key={accessProfileKey}
      recordingSchedule={recordingSchedule}
      onToggleRecording={handleToggleCameraRecording}
    />
  );

  return (
    <>
      <Router>
        <LocationsProvider>
        <LiveControlRoomProvider>
          <AppShell
            currentUser={currentUser}
            liveView={liveView}
            recordingSchedule={recordingSchedule}
            isRecordingEnabled={isRecordingEnabled}
            onScheduleChange={handleScheduleUpdate}
            onToggleMasterRecording={handleSetMasterRecording}
            onLogout={handleLogout}
            theme={theme}
            toggleTheme={toggleTheme}
            accessProfileKey={accessProfileKey}
          />
        </LiveControlRoomProvider>
        </LocationsProvider>
      </Router>
    </>
  );
}

function AppShell({
  currentUser,
  liveView,
  recordingSchedule,
  isRecordingEnabled,
  onScheduleChange,
  onToggleMasterRecording,
  onLogout,
  theme,
  toggleTheme,
  accessProfileKey,
}: {
  currentUser: User;
  liveView: React.ReactElement;
  recordingSchedule: RecordingScheduleType;
  isRecordingEnabled: boolean;
  onScheduleChange: (
    newSchedule: RecordingScheduleType,
    options?: { quiet?: boolean },
  ) => Promise<void>;
  onToggleMasterRecording: (isEnabled: boolean) => Promise<void>;
  onLogout: () => void;
  theme: 'light' | 'dark';
  toggleTheme: () => void;
  accessProfileKey: string;
}): React.ReactElement {
  const { controlRoom } = useLiveControlRoom();

  return (
    <>
        <div className="flex h-screen bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200">
          {!controlRoom && <Sidebar user={currentUser} />}
          <div className="flex flex-1 flex-col min-h-0 min-w-0">
            {!controlRoom && (
            <TopBar
              userName={currentUser.name}
              userRole={currentUser.role}
              onLogout={onLogout}
              theme={theme}
              toggleTheme={toggleTheme}
            />
            )}
            <main className={`flex-1 min-h-0 overflow-hidden flex flex-col ${
              controlRoom ? 'bg-black' : 'bg-gray-200 dark:bg-gray-900'
            }`}>
              <div className="flex-1 min-h-0 overflow-hidden relative">
              <ErrorBoundary>
              <Switch>
                <Route exact path="/" render={() => renderHome(currentUser, liveView)} />
                <Route path="/live" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, liveView)} />
                <Route path="/storage" render={() => renderSuperAdminOnly(currentUser, (
                  <Storage schedule={recordingSchedule} isRecordingEnabled={isRecordingEnabled} onScheduleChange={onScheduleChange} onToggleMasterRecording={onToggleMasterRecording}/>
                ))} />
                <Route exact path="/ptz" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, <PTZ />)} />
                <Route path="/ptz/:cameraId" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, <PTZ />)} />
                <Route path="/camera-management" render={() => (
                  isOpsAdminUser(currentUser)
                    ? <CameraManagement />
                    : <Redirect to={firstAllowedPath(currentUser) || '/live'} />
                )} />
                <Route path="/playback" render={() => renderProtected(currentUser, PERMISSIONS.RECORDING_VIEW, <Playback key={accessProfileKey} />, 'Playback')} />
                <Route path="/events" render={() => renderProtected(currentUser, PERMISSIONS.EVENTS, <Events />)} />
                <Route path="/network-settings" render={() => renderSuperAdminOnly(currentUser, <NetworkSettings />)} />
                <Route path="/user-management" render={() => (
                  isSuperAdminUser(currentUser)
                    ? <Redirect to="/control-center?tab=users" />
                    : isAdminUser(currentUser)
                      ? <UserManagement />
                      : <Redirect to={firstAllowedPath(currentUser) || '/live'} />
                )} />
                <Route path="/notifications" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <Notifications />, 'Alerts')} />
                <Route path="/system-status" render={() => renderSuperAdminOnly(currentUser, <SystemStatus />)} />
                <Route path="/go2rtc-diagnostics" render={() => renderSuperAdminOnly(currentUser, <Go2RtcDiagnostics />)} />
                <Route path="/maintenance" render={() => renderSuperAdminOnly(currentUser, <Maintenance />)} />
                <Route path="/control-center" render={() => renderControlCenter(currentUser, <ControlCenter currentUser={currentUser} />)} />
                <Route path="/super-admin" render={() => (
                  <Redirect to={isSuperAdminUser(currentUser) ? '/control-center' : (firstAllowedPath(currentUser) || '/live')} />
                )} />
                <Route><div className="text-center text-xl p-8">Page Not Found</div></Route>
              </Switch>
              </ErrorBoundary>
              </div>
            </main>
          </div>
        </div>
        {!controlRoom && (
        <Toaster position="top-right" toastOptions={{
          style: {
            background: theme === 'dark' ? '#333' : '#fff',
            color: theme === 'dark' ? '#fff' : '#333'
          }
        }}/>
        )}
    </>
  );
}
