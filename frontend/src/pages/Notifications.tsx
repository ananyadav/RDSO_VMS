import React, { useState, useEffect, useMemo } from "react";
import { AlertTriangle, Info, ShieldAlert, X, Trash2 } from "lucide-react";
import PageHeader from "../components/PageHeader";
import {
  useUrlHydration,
  useUrlSync,
  initialEnumParam,
} from "../hooks/useUrlSearchState";

interface Notification {
  id: number;
  type: "info" | "warning" | "error";
  message: string;
  time: string;
}

type NotificationFilter = "all" | "info" | "warning" | "error";

const initialNotifications: Notification[] = [
  { id: 1, type: "info", message: "System update v2.5.1 installed successfully.", time: "10:15 AM" },
  { id: 2, type: "warning", message: "Camera 'Driveway' has disconnected.", time: "10:25 AM" },
  { id: 3, type: "error", message: "Storage drive D: is 95% full.", time: "10:40 AM" },
  { id: 4, type: "info", message: "User 'admin' logged in from 192.168.1.50.", time: "11:02 AM" },
];

const notificationStyles = {
  info: { icon: Info, color: "text-blue-400" },
  warning: { icon: AlertTriangle, color: "text-yellow-400" },
  error: { icon: ShieldAlert, color: "text-red-400" },
};

const FILTER_OPTIONS: NotificationFilter[] = ["all", "info", "warning", "error"];

export default function Notifications(): React.ReactElement {
  const { setParams, initialParams, hydratedRef, markHydrated } = useUrlHydration();
  const [notifications, setNotifications] = useState<Notification[]>(initialNotifications);
  const [filter, setFilter] = useState<NotificationFilter>(() =>
    initialEnumParam(initialParams, "filter", FILTER_OPTIONS, "all"),
  );

  useEffect(() => {
    markHydrated();
  }, [markHydrated]);

  const urlValues = useMemo(
    () => ({ filter: filter === "all" ? null : filter }),
    [filter],
  );
  useUrlSync(hydratedRef, setParams, urlValues);

  const visible = filter === "all"
    ? notifications
    : notifications.filter((n) => n.type === filter);

  const dismiss = (id: number) => setNotifications((n) => n.filter((x) => x.id !== id));
  const clearAll = () => setNotifications([]);

  return (
    <div className="flex flex-col h-full space-y-4">
      <PageHeader
        title="System Notifications"
        subtitle="Review important system alerts and information"
        rightContent={
          <button
            onClick={clearAll}
            className="flex items-center text-sm font-semibold bg-red-600 hover:bg-red-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white px-4 py-2 rounded-md"
            disabled={notifications.length === 0}
          >
            <Trash2 size={16} className="mr-2" />
            Clear All
          </button>
        }
      />

      <div className="flex gap-2 px-1">
        {FILTER_OPTIONS.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setFilter(option)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md capitalize ${
              filter === option
                ? "bg-blue-600 text-white"
                : "bg-gray-800 text-gray-400 hover:text-white"
            }`}
          >
            {option}
          </button>
        ))}
      </div>
      
      <div className="bg-gray-800 border border-gray-700 rounded-lg">
        {visible.length === 0 ? (
          <p className="text-gray-500 text-center py-16">No notifications in this filter</p>
        ) : (
          <div className="divide-y divide-gray-700">
            {visible.map((n) => {
              const Style = notificationStyles[n.type];
              return (
                <div
                  key={n.id}
                  className="flex items-center justify-between p-4 hover:bg-gray-700/50 transition-colors"
                >
                  <div className="flex items-center space-x-4">
                    <Style.icon className={`${Style.color} w-6 h-6 shrink-0`} />
                    <div>
                      <p className="font-medium text-white">{n.message}</p>
                      <span className="text-xs text-gray-400">{n.time}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => dismiss(n.id)}
                    className="p-2 text-gray-500 hover:text-white hover:bg-gray-700 rounded-full"
                    title="Dismiss notification"
                  >
                    <X size={18} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
