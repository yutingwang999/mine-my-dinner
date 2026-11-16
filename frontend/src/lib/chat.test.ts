import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type ChatEvent,
  type ChatPlan,
  loadThread,
  normalizePlan,
  readEvents,
  sendMessage,
  storedThreadId,
  storeThreadId,
} from "@/lib/chat";
import { plan } from "@/test/chatFixtures";
import { mockFetch } from "@/test/helpers";

async function collect(response: Response): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const event of readEvents(response)) events.push(event);
  return events;
}

function streamOf(chunks: (string | Uint8Array)[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) =>
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk),
      );
      controller.close();
    },
  });
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("chat client", () => {
  it("parses events split across chunks", async () => {
    const chunks = [
      'event: progress\ndata: {"step": "par',
      'se"}\n\nevent: message\ndata: {"role":"assistant","te',
      'xt":"Hi 你好"}\n\nevent: done\n',
      "data: {}\n\n",
    ];
    expect(await collect(streamOf(chunks))).toEqual([
      { event: "progress", data: { step: "parse" } },
      { event: "message", data: { role: "assistant", text: "Hi 你好" } },
      { event: "done", data: {} },
    ]);
  });

  it("keeps a multi-byte character that is split between chunks", async () => {
    const bytes = new TextEncoder().encode(
      'event: message\ndata: {"role":"assistant","text":"汤"}\n\n',
    );
    const cut = bytes.indexOf(0xe6) + 1;
    const events = await collect(streamOf([bytes.slice(0, cut), bytes.slice(cut)]));
    expect(events).toEqual([{ event: "message", data: { role: "assistant", text: "汤" } }]);
  });

  it("posts the text and locale, and raises the error code of a failed request", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ code: "thread_not_found" }), { status: 404 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(sendMessage("t1", "hello", "zh")).rejects.toMatchObject({
      code: "thread_not_found",
      status: 404,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/threads/t1/messages",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: "hello", locale: "zh" }),
      }),
    );
  });

  it("survives storage that throws", () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
    expect(storedThreadId()).toBeNull();
    expect(() => storeThreadId("abc")).not.toThrow();
  });
});

describe("normalizePlan", () => {
  function withSpec(spec: Record<string, unknown>): ChatPlan {
    return { ...plan(), spec } as unknown as ChatPlan;
  }

  it("reads an old minimum split as 'at least' on each named side", () => {
    const old = withSpec({ mode: "shopping", dishes: 3, soups: 0, meat_dishes: 1, vegetable_dishes: 1, split_is_minimum: true });
    const { spec } = normalizePlan(old);
    expect([spec.meat_bound, spec.vegetable_bound]).toEqual(["at_least", "at_least"]);
    expect(spec).not.toHaveProperty("split_is_minimum");
  });

  it("reads an old exact split as exact and leaves an unnamed side unbound", () => {
    const old = withSpec({ mode: "shopping", dishes: 8, soups: 0, meat_dishes: 2, split_is_minimum: false });
    const { spec } = normalizePlan(old);
    expect([spec.meat_bound, spec.vegetable_bound]).toEqual(["exact", null]);
  });

  it("keeps the bounds of a current spec", () => {
    const current = withSpec({
      mode: "shopping",
      dishes: 3,
      soups: 0,
      meat_dishes: null,
      vegetable_dishes: 1,
      meat_bound: null,
      vegetable_bound: "at_most",
    });
    const { spec } = normalizePlan(current);
    expect([spec.meat_bound, spec.vegetable_bound]).toEqual([null, "at_most"]);
  });
});

describe("loadThread", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns items with each plan normalized and no extra card when a plan exists", async () => {
    const old = { ...plan() } as unknown as Record<string, unknown>;
    delete old.reasons;
    mockFetch({
      "GET /api/chat/threads/t": () => ({
        items: [
          { type: "user", text: "hi" },
          { type: "assistant", id: "a", text: "", plan: old },
          { type: "question", value: { kind: "request_style" } },
        ],
        messages: [],
        current_plan: plan(),
        pending: null,
      }),
    });
    const state = await loadThread("t");
    expect(state.items).toHaveLength(3);
    const reply = state.items[1];
    expect(reply.type === "assistant" && reply.plan?.reasons).toEqual([]);
  });

  it("appends a card-only item for current_plan when no item has a plan", async () => {
    mockFetch({
      "GET /api/chat/threads/t": () => ({
        items: [{ type: "assistant", id: "a", text: "old", plan: null }],
        current_plan: plan(),
        pending: null,
      }),
    });
    const state = await loadThread("t");
    expect(state.items).toHaveLength(2);
    expect(state.items[1]).toMatchObject({ type: "assistant", id: null, text: "" });
  });
});
