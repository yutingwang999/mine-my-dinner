"use client";

import { useTranslations } from "next-intl";

type Props = {
  /** The user's latest message, shown so the question is clear. */
  message: string;
  onChoose: (value: "revise" | "plan") => void;
  busy?: boolean;
};

export function ReviseOrPlanCard({ message, onChoose, busy = false }: Props) {
  const t = useTranslations("chat.reviseOrPlan");
  return (
    <section className="card">
      <p>{t("title")}</p>
      <p className="hint">{message}</p>
      <div className="actions">
        <button type="button" disabled={busy} onClick={() => onChoose("revise")}>
          {t("revise")}
        </button>
        <button type="button" disabled={busy} onClick={() => onChoose("plan")}>
          {t("plan")}
        </button>
      </div>
    </section>
  );
}
