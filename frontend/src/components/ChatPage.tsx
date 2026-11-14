"use client";

import { useFormatter, useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { PlanCard } from "@/components/PlanCard";
import { RequestStylePrompt } from "@/components/RequestStylePrompt";
import { ApiError } from "@/lib/api";
import {
  type ChatEvent,
  type ChatMessage,
  type ChatPlan,
  getRequestStyle,
  loadThread,
  type Locale,
  type NextAction,
  type Pending,
  putRequestStyle,
  readEvents,
  type RequestStyle,
  resume,
  sendMessage,
  startThread,
  storedThreadId,
  storeThreadId,
} from "@/lib/chat";
import { useErrorText } from "@/lib/errors";

type Seen = { plan: boolean; pending: boolean; failed: boolean };

const STYLES = ["ask_first", "plan_then_adjust"] as const;

function RequestStyleSetting({
  value,
  onChange,
}: {
  value: RequestStyle | null;
  onChange: (value: RequestStyle) => void;
}) {
  const t = useTranslations("chat.requestStyle");
  return (
    <label className="mini-field">
      {t("settingLabel")}
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value as RequestStyle)}>
        {value === null && (
          <option value="" disabled>
            {t("unset")}
          </option>
        )}
        {STYLES.map((style) => (
          <option key={style} value={style}>
            {t(style === "ask_first" ? "askFirst" : "planThenAdjust")}
          </option>
        ))}
      </select>
    </label>
  );
}

export function ChatPage() {
  const t = useTranslations("chat");
  const format = useFormatter();
  const errorText = useErrorText();
  const locale = useLocale() as Locale;
  const threadRef = useRef<string | null>(null);
  // Starts false so the first client render matches the server; the effect below sets it.
  const [loading, setLoading] = useState(false);
  const [style, setStyle] = useState<RequestStyle | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [plan, setPlan] = useState<ChatPlan | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const id = storedThreadId();
    if (id === null) return;
    let active = true;
    // Deferred so the effect body itself sets no state.
    Promise.resolve()
      .then(() => {
        if (active) setLoading(true);
        return loadThread(id);
      })
      .then((state) => {
        if (!active) return;
        threadRef.current = id;
        setMessages(state.messages);
        setPlan(state.current_plan);
        setPending(state.pending);
      })
      .catch((caught) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.code === "thread_not_found") {
          storeThreadId(null);
        } else {
          setError(caught);
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    getRequestStyle()
      .then((current) => active && setStyle(current))
      .catch((caught) => active && setError(caught));
    return () => {
      active = false;
    };
  }, []);

  async function changeStyle(next: RequestStyle) {
    try {
      setStyle(await putRequestStyle(next));
    } catch (caught) {
      setError(caught);
    }
  }

  function handle(event: ChatEvent, seen: Seen) {
    switch (event.event) {
      case "progress":
        setStep(event.data.step);
        break;
      case "token":
        setStreaming((current) => current + event.data.text);
        break;
      case "message":
        setStreaming("");
        setMessages((current) => [...current, { role: "assistant", text: event.data.text }]);
        break;
      case "plan":
        seen.plan = true;
        setPlan(event.data);
        break;
      case "interrupt":
        seen.pending = true;
        setPending(event.data);
        break;
      case "error":
        seen.failed = true;
        setError(new ApiError(0, event.data.code));
        break;
    }
  }

  async function run(start: (threadId: string) => Promise<Response>) {
    const seen: Seen = { plan: false, pending: false, failed: false };
    const previous = { plan, pending };
    setBusy(true);
    setError(null);
    setPending(null);
    setPlan(null);
    setStep(null);
    setStreaming("");
    try {
      if (threadRef.current === null) {
        threadRef.current = await startThread();
        storeThreadId(threadRef.current);
      }
      const response = await start(threadRef.current);
      for await (const event of readEvents(response)) handle(event, seen);
    } catch (caught) {
      seen.failed = true;
      setError(caught);
    } finally {
      // A failed run keeps what was on screen, so the next answer still resumes.
      if (seen.failed) {
        if (!seen.plan) setPlan(previous.plan);
        if (!seen.pending) setPending(previous.pending);
      }
      setBusy(false);
      setStep(null);
      setStreaming("");
    }
  }

  function submit(text: string) {
    const answering = pending?.kind === "question";
    setMessages((current) => [...current, { role: "user", text }]);
    return run((id) => (answering ? resume(id, text, locale) : sendMessage(id, text, locale)));
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy || loading) return;
    setDraft("");
    void submit(text);
  }

  function chooseStyle(value: RequestStyle | null) {
    if (value !== null) setStyle(value);
    void run((id) => resume(id, value, locale));
  }

  function newConversation() {
    threadRef.current = null;
    storeThreadId(null);
    setMessages([]);
    setPlan(null);
    setPending(null);
    setError(null);
    setDraft("");
  }

  const hint =
    pending?.kind === "question"
      ? t("question.hint", {
          fields: format.list(
            pending.fields.map((field) => (t.has(`question.${field}`) ? t(`question.${field}`) : field)),
            { type: "disjunction" },
          ),
        })
      : null;

  return (
    <div className="chat">
      <div className="actions">
        <RequestStyleSetting value={style} onChange={changeStyle} />
        <button type="button" disabled={busy} onClick={newConversation}>
          {t("newConversation")}
        </button>
      </div>
      {loading && <p className="hint">{t("loading")}</p>}
      <ul className="chat-log" aria-live="polite">
        {messages.map((message, index) => (
          <li key={index} className={`chat-message chat-${message.role}`}>
            <strong>{t(message.role === "user" ? "you" : "assistant")}</strong>
            <p>{message.text}</p>
          </li>
        ))}
        {streaming && (
          <li className="chat-message chat-assistant">
            <strong>{t("assistant")}</strong>
            <p>{streaming}</p>
          </li>
        )}
      </ul>
      {step && t.has(`progress.${step}`) && (
        <p role="status" className="hint">
          {t(`progress.${step}`)}
        </p>
      )}
      {plan && <PlanCard plan={plan} busy={busy} onAction={(action: NextAction) => submit(t(`action.${action}`))} />}
      {pending?.kind === "request_style" && <RequestStylePrompt busy={busy} onChoose={chooseStyle} />}
      {error !== null && (
        <p role="alert" className="notice notice-warning">
          {errorText(error)}
        </p>
      )}
      <form className="chat-form" onSubmit={onSubmit}>
        {hint && <p className="notice notice-info">{hint}</p>}
        <label className="stack-field">
          {t("inputLabel")}
          <input
            value={draft}
            maxLength={4000}
            placeholder={t("inputPlaceholder")}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || loading || draft.trim() === ""}>
          {t("send")}
        </button>
        <p className="hint">{t("providerNotice")}</p>
      </form>
    </div>
  );
}
