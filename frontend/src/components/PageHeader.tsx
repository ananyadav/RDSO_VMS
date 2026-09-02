import React from 'react';

export interface PageHeaderProps {
  title: string;
  /** Optional; omit for title-only headers (e.g. Live View). */
  subtitle?: string;
  rightContent?: React.ReactNode;
}

export default function PageHeader({ title, subtitle, rightContent }: PageHeaderProps) {
  return (
    <div className="flex-shrink-0 flex flex-col gap-1.5 sm:flex-row sm:justify-between sm:items-center sm:mb-1.5 mb-0">
      <div className="min-w-0">
        <h1 className="text-base sm:text-lg font-bold text-gray-900 dark:text-white leading-tight">
          {title}
        </h1>
        {subtitle != null && subtitle !== '' ? (
          <p className="hidden sm:block text-xs text-gray-500 dark:text-gray-400 truncate">
            {subtitle}
          </p>
        ) : null}
      </div>
      {rightContent ? (
        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 shrink-0">{rightContent}</div>
      ) : null}
    </div>
  );
}
