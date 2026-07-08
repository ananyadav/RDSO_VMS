import React, { useState, useEffect, useCallback, useRef } from 'react';
import { BrowserRouter as Router, Route, Switch, Redirect } from "react-router-dom";
import toast, { Toaster } from 'react-hot-toast';

// --- Hooks ---
import { useTheme } from './hooks/useTheme';

// --- Component & Layout Imports ---
import Sidebar from "./components/Sidebar";
import TopBar from "./components/TopBar";
import { renderProtected, renderHome } from "./components/ProtectedRoute";
import { PERMISSIONS } from './lib/permissions';

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
import type { User } from './services/authService';
import { authService } from './services/authService';
import { apiFetch } from './lib/api';
import { LocationsProvider } from './context/LocationsContext';
import ErrorBoundary from './components/ErrorBoundary';

type RecordingScheduleType = Record<string, boolean>;

export default function App(): React.ReactElement {
  // --- State Management ---
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
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
      const stored = authService.getCurrentUser();
      if (!stored) {
        setSessionReady(true);
        return;
      }

      const fresh = await authService.refreshSession();
      if (fresh) {
        setCurrentUser(fresh);
        beginSessionSync();
      } else {
        setCurrentUser(null);
        toast.error('Your session ended. Please log in again.');
      }
      setSessionReady(true);
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
    authService.logout();
    setCurrentUser(null);
    toast('You have been logged out.');
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
        <div className="flex h-screen bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200">
          <Sidebar user={currentUser} />
          <div className="flex flex-1 flex-col min-h-0">
            <TopBar
              userName={currentUser.name}
              userRole={currentUser.role}
              onLogout={handleLogout}
              theme={theme}
              toggleTheme={toggleTheme}
            />
            <main className="flex-1 min-h-0 overflow-hidden bg-gray-200 dark:bg-gray-900 flex flex-col">
              <div className="flex-1 min-h-0 overflow-hidden">
              <ErrorBoundary>
              <Switch>
                <Route exact path="/" render={() => renderHome(currentUser, liveView)} />
                <Route path="/live" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, liveView)} />
                <Route path="/storage" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, (
                  <Storage schedule={recordingSchedule} isRecordingEnabled={isRecordingEnabled} onScheduleChange={handleScheduleUpdate} onToggleMasterRecording={handleSetMasterRecording}/>
                ))} />
                <Route exact path="/ptz" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, <PTZ />)} />
                <Route path="/ptz/:cameraId" render={() => renderProtected(currentUser, PERMISSIONS.LIVE_VIEW, <PTZ />)} />
                <Route path="/camera-management" render={() => renderProtected(currentUser, PERMISSIONS.CAMERAS, <CameraManagement />, 'Camera Management')} />
                <Route path="/playback" render={() => renderProtected(currentUser, PERMISSIONS.PLAYBACK, <Playback key={accessProfileKey} />)} />
                <Route path="/events" render={() => renderProtected(currentUser, PERMISSIONS.EVENTS, <Events />)} />
                <Route path="/network-settings" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <NetworkSettings />, 'Network Settings')} />
                <Route path="/user-management" render={() => renderProtected(currentUser, PERMISSIONS.USERS, <UserManagement />, 'User Management')} />
                <Route path="/notifications" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <Notifications />, 'Alerts')} />
                <Route path="/system-status" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <SystemStatus />, 'System Status')} />
                <Route path="/go2rtc-diagnostics" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <Go2RtcDiagnostics />, 'go2rtc Diagnostics')} />
                <Route path="/maintenance" render={() => renderProtected(currentUser, PERMISSIONS.SYSTEM, <Maintenance />, 'Maintenance')} />
                <Route><div className="text-center text-xl p-8">Page Not Found</div></Route>
              </Switch>
              </ErrorBoundary>
              </div>
            </main>
          </div>
        </div>
        </LocationsProvider>
      </Router>
      <Toaster position="top-right" toastOptions={{
        style: {
          background: theme === 'dark' ? '#333' : '#fff',
          color: theme === 'dark' ? '#fff' : '#333'
        }
      }}/>
    </>
  );
}
