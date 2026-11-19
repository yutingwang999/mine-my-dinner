import {
  type Allergen,
  ApiError,
  type DishType,
  type Rule,
  type RuleType,
  type RuleWrite,
  type TimeSource,
} from "@/lib/api";
import type { Conflict } from "@/lib/planEdits";

export type Locale = "zh" | "en";
export type NextAction =
  | "switch_to_shopping"
  | "fewer_dishes"
  | "drop_time_limit"
  | "relax_split"
  | "relax_named"
  | "update_inventory"
  | "register_cookware"
  | "import_recipes";
export type RequestStyle = "ask_first" | "plan_then_adjust";

export type PlanFinding = {
  code: string;
  recipe_id: number | null;
  detail: Record<string, unknown>;
  /** The sentence for a hard check an edited plan allows (H1, H3, H6, H7). */
  text?: string;
};

/** How an edited plan differs from a count the user stated (backend edits._count_notes). */
export type CountNote = {
  field: "dishes" | "soups" | "meat_dishes" | "vegetable_dishes" | "staples" | "desserts" | "snacks";
  stated: number;
  now: number;
};

/** A quiet note on a rebuilt card: dishes dropped because they left the library. */
export type PlanNote = { field: "deleted_recipes"; titles: string[] };

/**
 * A manual version's overrides and edit log, the conflicts still to keep or remove, and the
 * conflicts its overrides cover (`kept`, named for display; absent on cards before it existed).
 */
export type PlanEdit = { overrides: unknown[]; log: unknown[]; conflicts: Conflict[]; kept?: Conflict[] };

/** The `current_plan` the planning graph presents (backend agent/graph.py present_plan). */
/** How a named meat or vegetable count binds (design 5.11). */
export type Bound = "exact" | "at_least" | "at_most";

/** A dish left out because an ingredient's allergens are unknown (backend _merge_notes). */
export type LeftOutDish = { id: number; title: string; lines: string[] };

/** One person on the plan's diners line (backend diners_line); the user has no name. */
export type PlanDiner = { name: string | null; is_self: boolean; guest: boolean };

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
  reasons: { reason: string; category: string | null; needed: number | null; available: number | null; text: string }[];
  next_actions: NextAction[];
  defaults_used: string[];
  spec: {
    mode: "fridge_only" | "shopping";
    dishes: number;
    soups: number;
    meat_dishes: number | null;
    vegetable_dishes: number | null;
    /** How a named side binds; null when that side is not named. */
    meat_bound: Bound | null;
    vegetable_bound: Bound | null;
    staples: number;
    desserts: number;
    snacks: number;
    time_limit_min: number | null;
    must_use: string[];
    all_use: string[];
    prefer_use: string[];
  };
  headcount: number;
  servings_notes: { recipe_id: number; servings: number; headcount: number }[];
  left_out_unknown_allergens: string[];
  /** Per left-out dish, the ingredient line names that caused it; absent before plan 2b. */
  left_out_details?: LeftOutDish[];
  /** Whose rules the plan was checked against: the user first, then friends, then guests. */
  diners?: PlanDiner[];
  /** People eating who match no diner; their limits are unknown. */
  others?: number;
  semantic_paused: boolean;
  passed: boolean;
  /** True on a manual version (plan 3a); absent on plans saved before manual edits. */
  edited?: boolean;
  /** The index of the model version this card edits or restores. */
  model_version?: number;
  edit?: PlanEdit;
  count_notes?: CountNote[];
  notes?: PlanNote[];
};

/** A rule the user stated in the chat, proposed on the diner card (backend agent/diners.py). */
export type ProposedRule = {
  rule_type: RuleType;
  allergen: Allergen | null;
  ingredient_id: number | null;
  /** The dictionary name the stated name matched, for display. */
  ingredient_name: string | null;
  text: string | null;
  /** A text rule, which matches recipe lines by wording and may match loosely. */
  by_wording: boolean;
};

/** The user (name null) or a saved friend on the diner card. */
export type DinerRow = {
  diner_id: number;
  name: string | null;
  is_self: boolean;
  selected: boolean;
  rules: Rule[];
  proposed: ProposedRule[];
  /** Diet words ("vegetarian") the person stated: shown as a hint, never as a rule. */
  diet_words: string[];
};

/** A person from the conversation who is not a saved friend. */
export type GuestRow = { name: string | null; proposed: ProposedRule[]; diet_words: string[] };

/** The diner card's interrupt value (backend build_card). */
export type DinersPending = {
  kind: "diners";
  headcount: number;
  rows: DinerRow[];
  guests: GuestRow[];
  /** People eating who match no row. */
  others: number;
  /** The request mentions a restriction, but no rule was parsed. */
  nudge: boolean;
};

/** A rule ticked or entered on the card; `save` writes it to the person's profile. */
export type RuleChoice = RuleWrite & { save: boolean };

export type GuestAnswer = { name: string | null; rules: RuleChoice[]; save: boolean };

/** The confirmed diner card (backend DinerAnswer). */
export type DinerAnswer = {
  selected: number[];
  stated: Record<number, RuleChoice[]>;
  guests: GuestAnswer[];
  others: number;
  others_answer: "none" | "unsure" | null;
  /** The card's confirmed total; the backend derives the unmatched people from it. */
  headcount?: number;
};

/** The history summary of a confirmed card: friends' and guests' names, and how many more. */
/** `self` is absent in histories saved before it existed (treated as false). */
export type DinerSummary = { self?: boolean; names: string[]; others: number };

export type Pending =
  | { kind: "request_style" }
  | DinersPending
  | {
      kind: "question";
      fields: string[];
      // split_exceeds_total stays for questions saved before split_conflicts_total existed.
      problem?: "split_conflicts_total" | "split_exceeds_total" | "too_many";
    };

export type ChatMessage = { role: "user" | "assistant"; text: string };

export type ChatEvent =
  | { event: "progress"; data: { step: string } }
  | { event: "message"; data: { role: "assistant"; text: string } }
  | { event: "plan"; data: ChatPlan }
  | { event: "interrupt"; data: Pending }
  | { event: "error"; data: { code: string } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: Record<string, never> };

/** One entry of the conversation as shown: a user line, an assistant reply (with its plan), or a question. */
export type ThreadItem =
  | { type: "user"; text: string }
  | { type: "assistant"; id: string | null; text: string; plan: ChatPlan | null }
  | { type: "question"; value: AskedQuestion };

/** A question as the history records it; the diner card keeps only a summary (null when skipped). */
export type AskedQuestion =
  | Exclude<Pending, { kind: "diners" }>
  | { kind: "diners"; summary: DinerSummary | null };

export type ThreadState = {
  items: ThreadItem[];
  pending: Pending | null;
  /** Whether the newest card takes manual edits now (no run in progress, nothing pending). */
  editable: boolean;
};

type RawThread = {
  items?: ThreadItem[];
  messages?: ChatMessage[];
  current_plan?: ChatPlan | null;
  pending?: Pending | null;
  editable?: boolean;
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

export function post(path: string, data?: unknown): Promise<Response> {
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

/** Answers a pending question with text, or the diner card with its answer; null skips it. */
export function resume(
  threadId: string,
  value: string | DinerAnswer | null,
  locale: Locale,
): Promise<Response> {
  return post(`/api/chat/threads/${threadId}/resume`, { value, locale });
}

export async function loadThread(threadId: string): Promise<ThreadState> {
  const raw = (await (await call(`/api/chat/threads/${threadId}`, { method: "GET" })).json()) as RawThread;
  const source: ThreadItem[] =
    raw.items ??
    (raw.messages ?? []).map(
      (message): ThreadItem =>
        message.role === "user"
          ? { type: "user", text: message.text }
          : { type: "assistant", id: null, text: message.text, plan: null },
    );
  const items = source.map(
    (item): ThreadItem =>
      item.type === "assistant" && item.plan ? { ...item, plan: normalizePlan(item.plan) } : item,
  );
  // A thread saved before plans were kept per reply: show its one plan as a card-only reply at the end.
  if (raw.current_plan && !items.some((item) => item.type === "assistant" && item.plan)) {
    items.push({ type: "assistant", id: null, text: "", plan: normalizePlan(raw.current_plan) });
  }
  return { items, pending: raw.pending ?? null, editable: raw.editable ?? false };
}

/** The history summary of a confirmed card, built as the backend's answer_summary builds it. */
export function dinerSummary(pending: DinersPending, answer: DinerAnswer): DinerSummary {
  const friends = new Map<number, string>();
  let selfId: number | null = null;
  for (const row of pending.rows) {
    if (row.is_self) selfId = row.diner_id;
    else if (row.name) friends.set(row.diner_id, row.name);
  }
  const names = answer.selected.flatMap((id) => {
    const name = friends.get(id);
    return name ? [name] : [];
  });
  names.push(...answer.guests.flatMap((guest) => (guest.name ? [guest.name] : [])));
  const unnamed = answer.guests.filter((guest) => !guest.name).length;
  return { self: selfId !== null && answer.selected.includes(selfId), names, others: answer.others + unnamed };
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

export const KNOWN_ACTIONS: readonly NextAction[] = [
  "switch_to_shopping",
  "fewer_dishes",
  "drop_time_limit",
  "relax_split",
  "relax_named",
  "update_inventory",
  "register_cookware",
  "import_recipes",
];

/**
 * Fills the fields a plan saved by an older version lacks, so the card can rely on the
 * current shape. An old S1 warning (one recipe, no `parts`) gets one part from it.
 */
export function normalizePlan(raw: ChatPlan): ChatPlan {
  const old = raw as unknown as Partial<ChatPlan> & {
    spec?: Partial<ChatPlan["spec"]> & { split_is_minimum?: boolean };
  };
  const spec: Partial<ChatPlan["spec"]> & { split_is_minimum?: boolean } = old.spec ?? {};
  const meat_dishes = spec.meat_dishes ?? null;
  const vegetable_dishes = spec.vegetable_dishes ?? null;
  // A spec saved before bounds existed: split_is_minimum made both named sides "at least".
  const oldBound: Bound = spec.split_is_minimum ? "at_least" : "exact";
  const bound = (count: number | null, value: Bound | null | undefined): Bound | null =>
    count === null ? null : (value ?? oldBound);
  return {
    ...raw,
    recipes: old.recipes ?? [],
    missing: old.missing ?? {},
    missing_cookware: old.missing_cookware ?? {},
    unmet: old.unmet ?? [],
    reasons: old.reasons ?? [],
    next_actions: old.next_actions ?? [],
    defaults_used: old.defaults_used ?? [],
    servings_notes: old.servings_notes ?? [],
    left_out_unknown_allergens: old.left_out_unknown_allergens ?? [],
    warnings: (old.warnings ?? []).map((finding) => {
      if (finding.code !== "S1" || Array.isArray(finding.detail?.parts)) return finding;
      const parts =
        finding.recipe_id === null
          ? []
          : [{ recipe_id: finding.recipe_id, amount: Number(finding.detail?.needed ?? 0) }];
      return { ...finding, detail: { ...finding.detail, parts } };
    }),
    spec: {
      mode: spec.mode ?? "fridge_only",
      dishes: spec.dishes ?? 0,
      soups: spec.soups ?? 0,
      meat_dishes,
      vegetable_dishes,
      meat_bound: bound(meat_dishes, spec.meat_bound),
      vegetable_bound: bound(vegetable_dishes, spec.vegetable_bound),
      staples: spec.staples ?? 0,
      desserts: spec.desserts ?? 0,
      snacks: spec.snacks ?? 0,
      time_limit_min: spec.time_limit_min ?? null,
      must_use: spec.must_use ?? [],
      all_use: spec.all_use ?? [],
      prefer_use: spec.prefer_use ?? [],
    },
  };
}
