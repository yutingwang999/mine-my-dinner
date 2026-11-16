"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";

import { ChatWelcome } from "@/components/ChatWelcome";
import { Composer } from "@/components/Composer";
import { MessageList } from "@/components/MessageList";
import { RequestStyleSwitch } from "@/components/RequestStyleSwitch";
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

/** Closer than this to the bottom counts as "at the bottom" for following new items. */
const NEAR_BOTTOM_PX = 80;

type Seen = {
  plan: boolean;
  pending: boolean;
  failed: boolean;
  /** The assistant item this run appended last; a `plan` event attaches to it. */
  assistant: ThreadItem | null;
};

export function ChatPage() {
  const t = useTranslations("chat");
  const locale = useLocale() as Locale;
  const threadRef = useRef<string | null>(null);
  // Starts false so the first client render matches the server; the effect below sets it.
  const [loading, setLoading] = useState(false);
  // False until the stored-thread check has run, so the welcome does not flash before a load starts.
  const [ready, setReady] = useState(false);
  const [style, setStyle] = useState<RequestStyle | null>(null);
  const [items, setItems] = useState<ThreadItem[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Follow the newest item, unless the user scrolled up to read.
  const followRef = useRef(true);

  useEffect(() => {
    const box = scrollRef.current;
    if (box && followRef.current) box.scrollTop = box.scrollHeight;
  }, [items, pending, step, streaming, error, loading]);

  function onScroll() {
    const box = scrollRef.current;
    if (box) followRef.current = box.scrollHeight - box.scrollTop - box.clientHeight <= NEAR_BOTTOM_PX;
  }

  useEffect(() => {
    const id = storedThreadId();
    let active = true;
    if (id === null) {
      // Deferred so the effect body itself sets no state.
      Promise.resolve().then(() => active && setReady(true));
      return () => {
        active = false;
      };
    }
    Promise.resolve()
      .then(() => {
        if (active) {
          setLoading(true);
          setReady(true);
        }
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
  async function run(
    start: (threadId: string) => Promise<Response>,
    answered: ThreadItem | null = null,
  ): Promise<boolean> {
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
    return !seen.failed;
  }

  function submit(text: string) {
    const answering = pending?.kind === "question";
    const answered: ThreadItem | null = answering && pending ? { type: "question", value: pending } : null;
    setItems((current) => [...current, ...(answered ? [answered] : []), { type: "user", text }]);
    return run((id) => (answering ? resume(id, text, locale) : sendMessage(id, text, locale)), answered);
  }

  function send() {
    const text = draft.trim();
    if (!text || busy || loading) return;
    setDraft("");
    followRef.current = true;
    void submit(text);
  }

  function chooseStyle(value: RequestStyle | null) {
    // Skipping saves "plan then adjust" on the server (design 6.2), so the setting shows it.
    const previousStyle = style;
    setStyle(value ?? "plan_then_adjust");
    const answered: ThreadItem | null = pending ? { type: "question", value: pending } : null;
    if (answered) setItems((current) => [...current, answered]);
    void run((id) => resume(id, value, locale), answered).then((ok) => {
      if (!ok) setStyle(previousStyle);
    });
  }

  function newConversation() {
    threadRef.current = null;
    followRef.current = true;
    storeThreadId(null);
    setItems([]);
    setPending(null);
    setError(null);
    setDraft("");
  }

  const welcome = ready && items.length === 0 && pending === null && !loading;

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        {!welcome && <h1 className="sr-only">{t("title")}</h1>}
        {loading && <p className="hint">{t("loading")}</p>}
        {welcome && (
          <ChatWelcome
            onPick={(text) => {
              setDraft(text);
              inputRef.current?.focus();
            }}
          />
        )}
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
      </div>
      <div className="chat-bottom">
        <Composer busy={busy || loading} onSend={send} value={draft} onChange={setDraft} textareaRef={inputRef} />
        <div className="chat-tools">
          <RequestStyleSwitch value={style} onChange={changeStyle} />
          <button type="button" className="composer-action" disabled={busy || loading} onClick={newConversation}>
            {t("newConversation")}
          </button>
        </div>
        <p className="hint">{t("providerNotice")}</p>
      </div>
    </div>
  );
}
