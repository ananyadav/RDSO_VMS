import React from 'react';

interface MasterToggleProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  mixed?: boolean;
}

/** iOS-style toggle with reliable knob slide animation. */
export default function MasterToggle({
  enabled,
  onChange,
  disabled = false,
  mixed = false,
}: MasterToggleProps): React.ReactElement {
  const on = mixed ? false : enabled;

  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => !disabled && onChange(!enabled)}
      disabled={disabled}
      title={mixed ? 'Partial — click to enable all' : undefined}
      className={`relative inline-flex h-7 w-12 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800 ${
        mixed ? 'bg-blue-400' : on ? 'bg-emerald-500' : 'bg-gray-600'
      } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
    >
      <span
        aria-hidden
        className={`pointer-events-none absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow-md transition-transform duration-200 ease-in-out ${
          mixed ? 'translate-x-2.5' : on ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  );
}
