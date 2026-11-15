"use client";

import { useLocale, useTranslations } from "next-intl";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { MessageList } from "@/components/MessageList";
import { ApiError } from "@/lib/api";
import {
  type ChatEvent,
  getRequestStyle,
  loadThread,
  type Locale,
  type NextAction,
  normalizePlan,
  type Pending,
  putRequestStyle,
  readEvents,
  type RequestStyle,
  resume,
  sendMessage,
  startThread,
  storedThreadId,
  storeThreadId,
  type ThreadItem,
} from "@/lib/chat";

/** The actions that are buttons sending a fixed message; the others are links. */
type FixedAction = Exclude<NextAction, "update_inventory" | "register_cookware" | "import_recipes">;

type Seen = {
  plan: boolean;
  pending: boolean;
  failed: boolean;
  /** The assistant item this run appended last; a `plan` event attaches to it. */
  assistant: ThreadItem | null;
};

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
  const locale = useLocale() as Locale;
  const threadRef = useRef<string | null>(null);
  // Starts false so the first client render matches the server; the effect below sets it.
  const [loading, setLoading] = useState(false);
  const [style, setStyle] = useState<RequestStyle | null>(null);
  const [items, setItems] = useState<ThreadItem[]>([]);
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
        setItems(state.items);
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
        {
          const item: ThreadItem = { type: "assistant", id: null, text: event.data.text, plan: null };
          seen.assistant = item;
          setItems((current) => [...current, item]);
        }
        break;
      case "plan": {
        seen.plan = true;
        const plan = normalizePlan(event.data);
        const old = seen.assistant;
        const next: ThreadItem =
          old?.type === "assistant"
            ? { ...old, plan }
            : { type: "assistant", id: null, text: "", plan };
        seen.assistant = next;
        setItems((current) =>
          old && current.includes(old) ? current.map((item) => (item === old ? next : item)) : [...current, next],
        );
        break;
      }
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

  /** `answered` is the question this run answers: it joins the conversation, and leaves again if the run fails. */
  async function run(start: (threadId: string) => Promise<Response>, answered: ThreadItem | null = null) {
    const seen: Seen = { plan: false, pending: false, failed: false, assistant: null };
    const previous = { pending };
    setBusy(true);
    setError(null);
    setPending(null);
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
        if (!seen.pending) {
          setPending(previous.pending);
          if (answered) setItems((current) => current.filter((item) => item !== answered));
        }
      }
      setBusy(false);
      setStep(null);
      setStreaming("");
    }
  }

  function submit(text: string) {
    const answering = pending?.kind === "question";
    const answered: ThreadItem | null = answering && pending ? { type: "question", value: pending } : null;
    setItems((current) => [...current, ...(answered ? [answered] : []), { type: "user", text }]);
    return run((id) => (answering ? resume(id, text, locale) : sendMessage(id, text, locale)), answered);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy || loading) return;
    setDraft("");
    void submit(text);
  }

  function chooseStyle(value: RequestStyle | null) {
    // Skipping saves "plan then adjust" on the server (design 6.2), so the setting shows it.
    setStyle(value ?? "plan_then_adjust");
    const answered: ThreadItem | null = pending ? { type: "question", value: pending } : null;
    if (answered) setItems((current) => [...current, answered]);
    void run((id) => resume(id, value, locale), answered);
  }

  function newConversation() {
    threadRef.current = null;
    storeThreadId(null);
    setItems([]);
    setPending(null);
    setError(null);
    setDraft("");
  }

  return (
    <div className="chat">
      <div className="actions">
        <RequestStyleSetting value={style} onChange={changeStyle} />
        <button type="button" disabled={busy} onClick={newConversation}>
          {t("newConversation")}
        </button>
      </div>
      {loading && <p className="hint">{t("loading")}</p>}
      <MessageList
        items={items}
        pending={pending}
        streaming={streaming}
        step={step}
        error={error}
        busy={busy}
        onAction={(action: NextAction) => submit(t(`actionMessage.${action as FixedAction}`))}
        onChooseStyle={chooseStyle}
      />
      <form className="chat-form" onSubmit={onSubmit}>
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
