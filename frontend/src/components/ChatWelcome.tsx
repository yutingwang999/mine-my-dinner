"use client";

import { useTranslations } from "next-intl";

const EXAMPLES = ["fridge", "shopping", "quick"] as const;

export function ChatWelcome({ onPick }: { onPick: (text: string) => void }) {
  const t = useTranslations();
  return (
    <section className="chat-welcome">
      <h1>{t("app.name")}</h1>
      <p>{t("app.tagline")}</p>
      <div className="chat-examples">
        {EXAMPLES.map((key) => (
          <button key={key} type="button" onClick={() => onPick(t(`chat.welcome.${key}`))}>
            {t(`chat.welcome.${key}`)}
          </button>
        ))}
      </div>
    </section>
  );
}
