import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type ChatEvent,
  type ChatPlan,
  type DinersPending,
  dinerSummary,
  loadThread,
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

describe("loadThread", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns the items, the pending question and the editable flag", async () => {
    mockFetch({
      "GET /api/chat/threads/t": () => ({
        items: [
          { type: "user", text: "hi" },
          { type: "assistant", id: "a", text: "", plan: plan() },
          { type: "question", value: { kind: "request_style" } },
        ],
        pending: null,
        editable: true,
      }),
    });
    const state = await loadThread("t");
    expect(state.items).toHaveLength(3);
    expect(state.editable).toBe(true);
    expect(state.pending).toBeNull();
    const reply = state.items[1];
    expect(reply.type === "assistant" && reply.plan?.passed).toBe(true);
  });
});

describe("dinerSummary", () => {
  const pending = {
    kind: "diners",
    headcount: 2,
    rows: [
      { diner_id: 1, name: null, is_self: true, selected: true, rules: [], proposed: [], diet_words: [] },
      { diner_id: 2, name: "Xiao Wang", is_self: false, selected: true, rules: [], proposed: [], diet_words: [] },
    ],
    guests: [],
    others: 0,
    nudge: false,
  } as DinersPending;
  const answer = (selected: number[]) => ({ selected, stated: {}, guests: [], others: 0, others_answer: null });

  it("includes the user when selected", () => {
    expect(dinerSummary(pending, answer([1, 2]))).toEqual({ self: true, names: ["Xiao Wang"], others: 0 });
  });

  it("marks the user as not eating when deselected", () => {
    expect(dinerSummary(pending, answer([2]))).toEqual({ self: false, names: ["Xiao Wang"], others: 0 });
  });
});
