import { ApiError, type DishType, type TimeSource } from "@/lib/api";

export type Locale = "zh" | "en";
export type NextAction = "switch_to_shopping" | "fewer_dishes" | "drop_time_limit";
export type RequestStyle = "ask_first" | "plan_then_adjust";

export type PlanFinding = {
  code: string;
  recipe_id: number | null;
  detail: Record<string, unknown>;
};

/** The `current_plan` the planning graph presents (backend agent/graph.py present_plan). */
export type ChatPlan = {
  recipes: {
    id: number;
    title: string;
    dish_type: DishType;
    time_min: number | null;
    time_source: TimeSource | null;
    servings: number | null;
  }[];
  missing: Record<string, string[]>;
  missing_cookware: Record<string, string[]>;
  warnings: PlanFinding[];
  unmet: { code: string; recipe_id: number | null; text: string }[];
  next_actions: NextAction[];
  defaults_used: string[];
  spec: { mode: "fridge_only" | "shopping"; dishes: number; soups: number; time_limit_min: number | null };
  headcount: number;
  servings_notes: { recipe_id: number; servings: number; headcount: number }[];
  left_out_unknown_allergens: string[];
  semantic_paused: boolean;
  passed: boolean;
};

export type Pending =
  | { kind: "request_style" }
  | { kind: "question"; fields: string[] };

export type ChatMessage = { role: "user" | "assistant"; text: string };

export type ChatEvent =
  | { event: "progress"; data: { step: string } }
  | { event: "message"; data: { role: "assistant"; text: string } }
  | { event: "plan"; data: ChatPlan }
  | { event: "interrupt"; data: Pending }
  | { event: "error"; data: { code: string } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: Record<string, never> };

export type ThreadState = {
  messages: ChatMessage[];
  current_plan: ChatPlan | null;
  pending: Pending | null;
};

export const THREAD_KEY = "mmd.chat.thread";

export function storedThreadId(): string | null {
  try {
    return window.localStorage.getItem(THREAD_KEY);
  } catch {
    return null;
  }
}

export function storeThreadId(threadId: string | null): void {
  try {
    if (threadId === null) {
      window.localStorage.removeItem(THREAD_KEY);
    } else {
      window.localStorage.setItem(THREAD_KEY, threadId);
    }
  } catch {
    // Without storage the conversation simply is not remembered after a reload.
  }
}

function parseFrame(frame: string): ChatEvent | null {
  let name = "";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!name) return null;
  try {
    return { event: name, data: data.length ? JSON.parse(data.join("\n")) : {} } as ChatEvent;
  } catch {
    return null;
  }
}

/** Parses the `event:`/`data:` frames of a server-sent-events body, however the chunks split. */
export async function* readEvents(response: Response): AsyncGenerator<ChatEvent> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const event = parseFrame(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (event) yield event;
        end = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    const last = buffer.trim() ? parseFrame(buffer) : null;
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}

async function call(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(response.status, typeof body.code === "string" ? body.code : "unknown", false, body);
  }
  return response;
}

function post(path: string, data?: unknown): Promise<Response> {
  return call(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
}

export async function startThread(): Promise<string> {
  const body = (await (await post("/api/chat/threads")).json()) as { thread_id: string };
  return body.thread_id;
}

/** Sends a message; the response is an event stream for `readEvents`. */
export function sendMessage(threadId: string, text: string, locale: Locale): Promise<Response> {
  return post(`/api/chat/threads/${threadId}/messages`, { text, locale });
}

/** Answers a pending question; a null value skips it. */
export function resume(threadId: string, value: unknown, locale: Locale): Promise<Response> {
  return post(`/api/chat/threads/${threadId}/resume`, { value, locale });
}

export async function loadThread(threadId: string): Promise<ThreadState> {
  return (await (await call(`/api/chat/threads/${threadId}`, { method: "GET" })).json()) as ThreadState;
}

export async function getRequestStyle(): Promise<RequestStyle | null> {
  const body = (await (await call("/api/preferences/request-style", { method: "GET" })).json()) as {
    value: RequestStyle | null;
  };
  return body.value;
}

export async function putRequestStyle(value: RequestStyle): Promise<RequestStyle | null> {
  const response = await call("/api/preferences/request-style", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  return ((await response.json()) as { value: RequestStyle | null }).value;
}
