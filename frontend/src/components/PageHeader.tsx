import React from 'react';

export interface PageHeaderProps {
  title: string;
  /** Optional; omit for title-only headers (e.g. Live View). */
  subtitle?: string;
  rightContent?: React.ReactNode;
}

export default function PageHeader({ title, subtitle, rightContent }: PageHeaderProps) {
  return (
    <div className="flex-shrink-0 flex justify-between items-center mb-1.5">
      <div>
        <h1 className="text-lg font-bold text-gray-900 dark:text-white">{title}</h1>
        {subtitle != null && subtitle !== '' ? (
          <p className="text-xs text-gray-500 dark:text-gray-400">{subtitle}</p>
        ) : null}
      </div>
      {rightContent ? <div>{rightContent}</div> : null}
    </div>
  );
}
