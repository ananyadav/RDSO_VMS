import React, { useEffect, useMemo, useState } from 'react';
import PageHeader from '../components/PageHeader';
import TCPIPSettings from '../components/TCPIPSettings';
import EmailSettings from '../components/EmailSettings';
import {
  useUrlHydration,
  useUrlSync,
  initialEnumParam,
} from '../hooks/useUrlSearchState';

const TABS = ['TCP/IP', 'Email', 'DDNS', 'NTP'] as const;
type NetworkTab = (typeof TABS)[number];

export default function NetworkSettings(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [activeTab, setActiveTab] = useState<NetworkTab>(() =>
    initialEnumParam(initialParams, 'tab', TABS, TABS[0]),
  );

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({ tab: activeTab === TABS[0] ? null : activeTab }),
    [activeTab],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  return (
    <div className="flex flex-col h-full space-y-4">
      <PageHeader
        title="Network Settings"
        subtitle="Configure IP, DDNS, Email, and other network services"
      />

      <div className="flex flex-col lg:flex-row gap-6">
        <div className="lg:w-1/4">
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-2 sticky top-6">
            <nav className="flex flex-col space-y-1">
              {TABS.map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-4 py-2 text-sm font-medium text-left rounded-md transition-colors ${
                    activeTab === tab
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </nav>
          </div>
        </div>

        <div className="lg:w-3/4">
          {activeTab === 'TCP/IP' && <TCPIPSettings />}
          {activeTab === 'Email' && <EmailSettings />}
          {activeTab !== 'TCP/IP' && activeTab !== 'Email' && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-6">
              <h3 className="text-xl font-bold text-white">{activeTab} Settings</h3>
              <p className="text-gray-400 mt-2">Configuration for {activeTab} will be available here.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
