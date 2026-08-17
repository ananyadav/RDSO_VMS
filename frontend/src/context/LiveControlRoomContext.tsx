import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

interface LiveControlRoomContextValue {
  controlRoom: boolean;
  setControlRoom: (value: boolean) => void;
}

const LiveControlRoomContext = createContext<LiveControlRoomContextValue | null>(null);

export function LiveControlRoomProvider({ children }: { children: React.ReactNode }): React.ReactElement {
  const [controlRoom, setControlRoomState] = useState(false);
  const setControlRoom = useCallback((value: boolean) => {
    setControlRoomState(value);
  }, []);
  const value = useMemo(() => ({ controlRoom, setControlRoom }), [controlRoom, setControlRoom]);
  return (
    <LiveControlRoomContext.Provider value={value}>{children}</LiveControlRoomContext.Provider>
  );
}

export function useLiveControlRoom(): LiveControlRoomContextValue {
  const ctx = useContext(LiveControlRoomContext);
  if (!ctx) {
    throw new Error('useLiveControlRoom must be used within LiveControlRoomProvider');
  }
  return ctx;
}
