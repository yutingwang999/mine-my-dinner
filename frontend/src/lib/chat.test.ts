import { afterEach, describe, expect, it, vi } from "vitest";

import { type ChatEvent, readEvents, sendMessage, storedThreadId, storeThreadId } from "@/lib/chat";

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
