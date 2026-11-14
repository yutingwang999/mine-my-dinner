"use client";

import { useTranslations } from "next-intl";

import type { RequestStyle } from "@/lib/chat";

type Props = {
  /** A null value skips the question. */
  onChoose: (value: RequestStyle | null) => void;
  busy?: boolean;
};

export function RequestStylePrompt({ onChoose, busy = false }: Props) {
  const t = useTranslations("chat.requestStyle");
  return (
    <section className="card">
      <p>{t("title")}</p>
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => onChoose("ask_first")}>
          {t("askFirst")}
        </button>
        <button type="button" disabled={busy} onClick={() => onChoose("plan_then_adjust")}>
          {t("planThenAdjust")}
        </button>
        <button type="button" disabled={busy} onClick={() => onChoose(null)}>
          {t("skip")}
        </button>
      </div>
    </section>
  );
}
