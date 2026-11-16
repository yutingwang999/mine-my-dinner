"use client";

import type { ReactNode } from "react";

/** The underline tab bar. Pair it with TabPanel using the same idPrefix. */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
  idPrefix,
}: {
  tabs: { value: T; label: string }[];
  active: T | null;
  onChange: (value: T) => void;
  label: string;
  idPrefix: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${tab.value}`}
          aria-selected={active === tab.value}
          aria-controls={`${idPrefix}-panel`}
          className={active === tab.value ? "tab active" : "tab"}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({
  idPrefix,
  active,
  children,
}: {
  idPrefix: string;
  active: string | null;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel`}
      aria-labelledby={active ? `${idPrefix}-tab-${active}` : undefined}
    >
      {children}
    </div>
  );
}
