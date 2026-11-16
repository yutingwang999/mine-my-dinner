"use client";

import { useFormatter, useTranslations } from "next-intl";

import { PlanCard } from "@/components/PlanCard";
import { RequestStylePrompt } from "@/components/RequestStylePrompt";
import type { NextAction, Pending, RequestStyle, ThreadItem } from "@/lib/chat";
import { useErrorText } from "@/lib/errors";

type Props = {
  items: ThreadItem[];
  pending: Pending | null;
  streaming: string;
  step: string | null;
  error: unknown;
  busy: boolean;
  onAction: (action: NextAction) => void;
  onChooseStyle: (value: RequestStyle | null) => void;
};

/** The assistant's words for a question: the hint that used to sit above the input. */
function useQuestionText() {
  const t = useTranslations("chat");
  const format = useFormatter();
  return (value: Extract<Pending, { kind: "question" }>): string =>
    value.problem && t.has(`problem.${value.problem}`)
      ? t(`problem.${value.problem}`)
      : t("question.hint", {
          fields: format.list(
            value.fields.map((field) => (t.has(`question.${field}`) ? t(`question.${field}`) : field)),
            { type: "disjunction" },
          ),
        });
}

export function MessageList({ items, pending, streaming, step, error, busy, onAction, onChooseStyle }: Props) {
  const t = useTranslations("chat");
  const tStyle = useTranslations("chat.requestStyle");
  const errorText = useErrorText();
  const questionText = useQuestionText();

  // The newest plan stays actionable until a newer one replaces it (design 5.1).
  let newestPlan = -1;
  items.forEach((item, index) => {
    if (item.type === "assistant" && item.plan) newestPlan = index;
  });

  return (
    <div className="chat-thread">
      <ul className="chat-log" aria-live="polite">
        {items.map((item, index) => {
          if (item.type === "user") {
            return (
              <li key={index} className="chat-message chat-user">
                <strong className="sr-only">{t("you")}</strong>
                <p>{item.text}</p>
              </li>
            );
          }
          if (item.type === "question") {
            return (
              <li key={index} className="chat-message chat-assistant">
                <strong className="sr-only">{t("assistant")}</strong>
                <p>{item.value.kind === "request_style" ? tStyle("title") : questionText(item.value)}</p>
              </li>
            );
          }
          return (
            <li key={index} className="chat-message chat-assistant">
              <strong className="sr-only">{t("assistant")}</strong>
              {item.text && <p>{item.text}</p>}
              {item.plan && (
                <PlanCard
                  plan={item.plan}
                  busy={busy || pending !== null}
                  actionable={index === newestPlan}
                  onAction={onAction}
                />
              )}
            </li>
          );
        })}
        {pending?.kind === "question" && (
          <li className="chat-message chat-assistant">
            <strong className="sr-only">{t("assistant")}</strong>
            <p>{questionText(pending)}</p>
          </li>
        )}
        {pending?.kind === "request_style" && (
          <li className="chat-message chat-assistant">
            <strong className="sr-only">{t("assistant")}</strong>
            <RequestStylePrompt busy={busy} onChoose={onChooseStyle} />
          </li>
        )}
      </ul>
      {/* Outside the live log: partial text must not be announced; the final message is. */}
      {streaming && (
        <div className="chat-message chat-assistant" aria-live="off">
          <strong className="sr-only">{t("assistant")}</strong>
          <p>{streaming}</p>
        </div>
      )}
      {step && t.has(`progress.${step}`) && (
        <p role="status" className="chat-message chat-assistant hint">
          {t(`progress.${step}`)}
        </p>
      )}
      {error !== null && (
        <p role="alert" className="chat-message chat-assistant notice notice-warning">
          {errorText(error)}
        </p>
      )}
    </div>
  );
}
