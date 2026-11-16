"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";

import type { RequestStyle } from "@/lib/chat";

type Props = {
  /** Null (no saved choice) shows the switch off. */
  value: RequestStyle | null;
  onChange: (value: RequestStyle) => void;
};

export function RequestStyleSwitch({ value, onChange }: Props) {
  const t = useTranslations("chat.requestStyle");
  const id = useId();
  const on = value === "ask_first";

  return (
    <div className="style-switch">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-meaning`}
        className="switch"
        onClick={() => onChange(on ? "plan_then_adjust" : "ask_first")}
      >
        <span className="switch-knob" aria-hidden="true" />
      </button>
      <span id={`${id}-label`}>{t("switchLabel")}</span>
      <span id={`${id}-meaning`} className="hint">
        {on ? t("askFirst") : t("planThenAdjust")}
      </span>
    </div>
  );
}
