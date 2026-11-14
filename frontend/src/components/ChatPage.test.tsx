import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "../../messages/en.json";
import { ChatPage } from "@/components/ChatPage";
import { THREAD_KEY } from "@/lib/chat";
import { plan } from "@/test/chatFixtures";
import { type Call, callsTo, mockFetch, renderWithProviders, reply } from "@/test/helpers";

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function stream(...frames: string[]): Response {
  return new Response(frames.join(""), { headers: { "Content-Type": "text/event-stream" } });
}

const DONE = frame("done", {});

let calls: Call[];

function setup(routes: Parameters<typeof mockFetch>[0] = {}) {
  calls = mockFetch({
    "GET /api/preferences/request-style": () => ({ value: null }),
    "POST /api/chat/threads": () => ({ thread_id: "t1" }),
    ...routes,
  });
  renderWithProviders(<ChatPage />);
}

async function send(text: string) {
  await userEvent.type(screen.getByRole("textbox", { name: "Your message" }), text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("ChatPage", () => {
  it("shows the fixed provider notice under the input", () => {
    setup();
    expect(screen.getByText(/sent to an AI model provider to plan meals/)).toBeInTheDocument();
  });

  it("starts a thread on the first message, remembers it, and shows the reply and plan", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(
          frame("progress", { step: "parse" }),
          frame("message", { role: "assistant", text: "Here is a plan." }),
          frame("plan", plan()),
          DONE,
        ),
    });
    await send("dinner for me");
    expect(await screen.findByText("Here is a plan.")).toBeInTheDocument();
    expect(screen.getByText("dinner for me")).toBeInTheDocument();
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(window.localStorage.getItem(THREAD_KEY)).toBe("t1");
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")[0].body).toEqual({
      text: "dinner for me",
      locale: "en",
    });
  });

  it("shows a one-line status while the run progresses", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              controller = c;
              c.enqueue(encoder.encode(frame("progress", { step: "search" })));
            },
          }),
        ),
    });
    await send("hi");
    expect(await screen.findByText("Searching your recipes…")).toBeInTheDocument();
    controller.enqueue(encoder.encode(frame("message", { role: "assistant", text: "Done." }) + DONE));
    controller.close();
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("Searching your recipes…")).toBeNull());
  });

  it("asks the request-style question and resumes with the choice", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () =>
        stream(frame("message", { role: "assistant", text: "Planning with your choice." }), DONE),
    });
    await send("dinner");
    expect(await screen.findByRole("button", { name: "Ask me first" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Plan, then I adjust" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ask me first" }));
    expect(await screen.findByText("Planning with your choice.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")[0].body).toEqual({
      value: "ask_first",
      locale: "en",
    });
    expect(screen.queryByRole("button", { name: "Ask me first" })).toBeNull();
  });

  it("resumes with null when the request-style question is skipped", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () => stream(DONE),
    });
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")[0].body).toEqual({
      value: null,
      locale: "en",
    });
  });

  it("sends the next message as the answer to a pending question", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "question", fields: ["mode"] }), DONE),
      "POST /api/chat/threads/t1/resume": () =>
        stream(frame("message", { role: "assistant", text: "Thanks." }), DONE),
    });
    await send("dinner");
    expect(await screen.findByText(/whether to cook only from the fridge/)).toBeInTheDocument();
    await send("shopping is fine");
    expect(await screen.findByText("Thanks.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")[0].body).toEqual({
      value: "shopping is fine",
      locale: "en",
    });
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")).toHaveLength(1);
    expect(screen.queryByText(/whether to cook only from the fridge/)).toBeNull();
  });

  it("restores the conversation and a pending question after reload", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        messages: [
          { role: "user", text: "dinner" },
          { role: "assistant", text: "Earlier reply." },
        ],
        current_plan: plan(),
        pending: { kind: "question", fields: ["composition"] },
      }),
      "POST /api/chat/threads/t9/resume": () => stream(DONE),
    });
    expect(await screen.findByText("Earlier reply.")).toBeInTheDocument();
    expect(screen.getByText("dinner")).toBeInTheDocument();
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText(/how many dishes you want/)).toBeInTheDocument();
    await send("two");
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t9/resume")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/chat/threads")).toHaveLength(0);
  });

  it("restores a pending request-style question", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        messages: [{ role: "user", text: "dinner" }],
        current_plan: null,
        pending: { kind: "request_style" },
      }),
    });
    expect(await screen.findByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("starts a new thread when the remembered one no longer exists", async () => {
    window.localStorage.setItem(THREAD_KEY, "gone");
    setup({
      "GET /api/chat/threads/gone": () => reply(404, { code: "thread_not_found" }),
      "POST /api/chat/threads/t1/messages": () => stream(DONE),
    });
    await waitFor(() => expect(window.localStorage.getItem(THREAD_KEY)).toBeNull());
    await send("hello");
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")).toHaveLength(1));
  });

  it("a next-action button sends its fixed message", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": (body) =>
        (body as { text: string }).text === "Allow shopping"
          ? stream(frame("message", { role: "assistant", text: "Shopping allowed." }), DONE)
          : stream(
              frame("message", { role: "assistant", text: "Closest plan." }),
              frame("plan", plan({ passed: false, next_actions: ["switch_to_shopping"] })),
              DONE,
            ),
    });
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Allow shopping" }));
    expect(await screen.findByText("Shopping allowed.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")[1].body).toEqual({
      text: "Allow shopping",
      locale: "en",
    });
  });

  it("shows a localized message for an error event and stays usable", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("error", { code: "models_unavailable" }), DONE),
    });
    await send("dinner");
    expect(await screen.findByRole("alert")).toHaveTextContent("No model is available right now");
    expect(screen.getByRole("textbox", { name: "Your message" })).toBeEnabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Your message" }), "try again");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("shows an internal error message", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () => stream(frame("error", { code: "internal" }), DONE),
    });
    await send("dinner");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong while planning");
  });

  it("appends reserved token events to the streaming text", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(
          frame("token", { text: "Hel" }),
          frame("token", { text: "lo" }),
          frame("message", { role: "assistant", text: "Hello" }),
          DONE,
        ),
    });
    await send("hi");
    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getAllByText(/Hel/)).toHaveLength(1);
  });

  it("starts a new conversation and forgets the old thread", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        messages: [{ role: "assistant", text: "Old reply." }],
        current_plan: null,
        pending: null,
      }),
    });
    expect(await screen.findByText("Old reply.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.queryByText("Old reply.")).toBeNull();
    expect(window.localStorage.getItem(THREAD_KEY)).toBeNull();
  });

  it("changes the request-style setting", async () => {
    setup({
      "GET /api/preferences/request-style": () => ({ value: "ask_first" }),
      "PUT /api/preferences/request-style": (body) => body,
    });
    const select = await screen.findByRole("combobox", { name: "When details are missing" });
    await waitFor(() => expect(select).toHaveValue("ask_first"));
    await userEvent.selectOptions(select, "plan_then_adjust");
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/preferences/request-style")).toHaveLength(1));
    expect(callsTo(calls, "PUT", "/api/preferences/request-style")[0].body).toEqual({
      value: "plan_then_adjust",
    });
    await waitFor(() => expect(select).toHaveValue("plan_then_adjust"));
  });

  it("updates the setting when the request-style prompt is answered, and leaves it on Skip", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () => stream(DONE),
    });
    const select = await screen.findByRole("combobox", { name: "When details are missing" });
    expect(select).toHaveValue("");
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Plan, then I adjust" }));
    await waitFor(() => expect(select).toHaveValue("plan_then_adjust"));
  });

  it("keeps the setting unchanged when the prompt is skipped", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () => stream(DONE),
    });
    const select = await screen.findByRole("combobox", { name: "When details are missing" });
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")).toHaveLength(1));
    expect(select).toHaveValue("");
  });

  it("keeps the plan and the pending question when a run fails with an error event", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    let attempt = 0;
    setup({
      "GET /api/chat/threads/t9": () => ({
        messages: [{ role: "assistant", text: "Which?" }],
        current_plan: plan(),
        pending: { kind: "question", fields: ["mode"] },
      }),
      "POST /api/chat/threads/t9/resume": () =>
        ++attempt === 1 ? stream(frame("error", { code: "internal" }), DONE) : stream(DONE),
    });
    await screen.findByText(/whether to cook only from the fridge/);
    await send("shopping");
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong while planning");
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText(/whether to cook only from the fridge/)).toBeInTheDocument();
    await send("shopping again");
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t9/resume")).toHaveLength(2));
    expect(callsTo(calls, "POST", "/api/chat/threads/t9/messages")).toHaveLength(0);
  });

  it("keeps the plan and the pending question when the request fails", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        messages: [],
        current_plan: plan(),
        pending: { kind: "question", fields: ["mode"] },
      }),
      "POST /api/chat/threads/t9/resume": () => reply(500, { code: "internal" }),
    });
    await screen.findByText(/whether to cook only from the fridge/);
    await send("shopping");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText(/whether to cook only from the fridge/)).toBeInTheDocument();
  });

  it("renders the same first markup as the server when a thread is remembered", () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    calls = mockFetch({
      "GET /api/preferences/request-style": () => ({ value: null }),
      "GET /api/chat/threads/t9": () => new Promise(() => {}) as never,
    });
    const first = renderToString(
      <NextIntlClientProvider locale="en" messages={en}>
        <ChatPage />
      </NextIntlClientProvider>,
    );
    expect(first).not.toContain("Loading the conversation");
  });
});
