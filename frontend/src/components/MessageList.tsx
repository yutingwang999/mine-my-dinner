"use client";

import { useFormatter, useTranslations } from "next-intl";

import { DinerCard, type DinerDraft } from "@/components/DinerCard";
import { PlanCard } from "@/components/PlanCard";
import { RequestStylePrompt } from "@/components/RequestStylePrompt";
import type {
  AskedQuestion,
  ChatPlan,
  DinerAnswer,
  NextAction,
  Pending,
  RequestStyle,
  ThreadItem,
} from "@/lib/chat";
import { useErrorText } from "@/lib/errors";

type Props = {
  items: ThreadItem[];
  pending: Pending | null;
  streaming: string;
  step: string | null;
  error: unknown;
  busy: boolean;
  onAction: (action: NextAction) => void;
  /** Sends the fixed "plan again" message after a left-out ingredient is confirmed. */
  onReplan: () => void;
  onChooseStyle: (value: RequestStyle | null) => void;
  onConfirmDiners: (answer: DinerAnswer) => void;
  /** The pending card's edits, kept across a failed confirm. */
  dinerDraft?: DinerDraft;
  onDinerDraft?: (draft: DinerDraft) => void;
  /** The thread takes manual edits of its newest card now (the thread read's `editable`). */
  editable?: boolean;
  threadId?: string | null;
  /** Gets the card a manual edit of the newest plan returned. */
  onEdited?: (plan: ChatPlan) => void;
  /** A manual edit was refused because the thread is busy. */
  onEditRefused?: () => void;
  /** Hears when a manual edit request starts (true) and ends (false). */
  onEditing?: (saving: boolean) => void;
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

/** The history line of an answered diner card: who was confirmed (the user as "me"). */
function useDinersText() {
  const t = useTranslations("chat.diners");
  const format = useFormatter();
  return (value: Extract<AskedQuestion, { kind: "diners" }>): string => {
    const summary = value.summary;
    if (!summary) return t("asked");
    const parts = [...(summary.self ? [t("summarySelf")] : []), ...summary.names];
    if (summary.others > 0) parts.push(t("more", { count: summary.others }));
    return parts.length ? t("answeredNames", { names: format.list(parts, { type: "conjunction" }) }) : t("answered");
  };
}

export function MessageList({
  items,
  pending,
  streaming,
  step,
  error,
  busy,
  onAction,
  onReplan,
  onChooseStyle,
  onConfirmDiners,
  dinerDraft,
  onDinerDraft,
  editable = false,
  threadId = null,
  onEdited,
  onEditRefused,
  onEditing,
}: Props) {
  const t = useTranslations("chat");
  const tEdit = useTranslations("chat.edit");
  const tStyle = useTranslations("chat.requestStyle");
  const errorText = useErrorText();
  const questionText = useQuestionText();
  const dinersText = useDinersText();

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
                <p>
                  {item.value.kind === "request_style"
                    ? tStyle("title")
                    : item.value.kind === "diners"
                      ? dinersText(item.value)
                      : questionText(item.value)}
                </p>
              </li>
            );
          }
          return (
            <li key={index} className="chat-message chat-assistant">
              <strong className="sr-only">{t("assistant")}</strong>
              {item.text && <p>{item.text}</p>}
              {/* The model's note was written for the plan it made, not the edited one. */}
              {item.text && item.plan?.edited && <p className="hint">{tEdit("noteForOriginal")}</p>}
              {item.plan && (
                <PlanCard
                  plan={item.plan}
                  busy={busy || pending !== null}
                  actionable={index === newestPlan}
                  onAction={onAction}
                  onReplan={onReplan}
                  editable={editable && index === newestPlan}
                  threadId={threadId}
                  onEdited={onEdited}
                  onEditRefused={onEditRefused}
                  onEditing={onEditing}
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
        {pending?.kind === "diners" && (
          <li className="chat-message chat-assistant chat-wide">
            <strong className="sr-only">{t("assistant")}</strong>
            <DinerCard
              pending={pending}
              busy={busy}
              onConfirm={onConfirmDiners}
              draft={dinerDraft}
              onDraft={onDinerDraft}
            />
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
