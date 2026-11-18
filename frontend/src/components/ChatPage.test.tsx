import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "../../messages/en.json";
import { ChatPage } from "@/components/ChatPage";
import { type NextAction, THREAD_KEY } from "@/lib/chat";
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
    // The question stays in place after it is answered.
    expect(screen.getAllByText(/whether to cook only from the fridge/)).toHaveLength(1);
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

  it("plan again sends the fixed replan message after an allergen is confirmed", async () => {
    const entry = {
      id: 9, name_zh: "花椒油", name_en: null, aliases_zh: [], aliases_en: [], user_aliases_zh: [],
      user_aliases_en: [], source: "user", category: "seasoning", stock_mode: "level", allergens: null,
      parent_id: null,
    };
    setup({
      "POST /api/chat/threads/t1/messages": (body) =>
        (body as { text: string }).text === "Plan again with the same request"
          ? stream(frame("message", { role: "assistant", text: "Planned again." }), DONE)
          : stream(
              frame("plan", {
                ...plan({ recipes: [], passed: false, left_out_unknown_allergens: ["宫保鸡丁"] }),
                left_out_details: [{ id: 11, title: "宫保鸡丁", lines: ["花椒油"] }],
              }),
              DONE,
            ),
      "POST /api/ingredients/match": () => ({ matches: [{ name: "花椒油", entry }] }),
      "POST /api/allergen-suggestions": () => ({ suggested: [], category: null, stock_mode: null }),
      "POST /api/allergens/resolve": () => entry,
    });
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Confirm allergens" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm it contains none" }));
    await userEvent.click(screen.getByRole("button", { name: "Save allergens" }));
    await userEvent.click(await screen.findByRole("button", { name: "Plan again" }));
    expect(await screen.findByText("Planned again.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")[1].body).toEqual({
      text: "Plan again with the same request",
      locale: "en",
    });
  });

  it("has the backend's replan wording in both languages", async () => {
    const zh = (await import("../../messages/zh.json")).default;
    expect(zh.chat.replanMessage).toBe("按同样的要求重新规划");
    expect(en.chat.replanMessage).toBe("Plan again with the same request");
  });

  it.each<[NextAction, string, string]>([
    ["relax_split", "Any mix is fine", "Any mix of meat and vegetable dishes is fine"],
    ["relax_named", "Named ingredients optional", "The named ingredients are optional"],
  ])("sends the fixed %s message", async (action, label, text) => {
    setup({
      "POST /api/chat/threads/t1/messages": (body) =>
        (body as { text: string }).text === text
          ? stream(frame("message", { role: "assistant", text: "Relaxed." }), DONE)
          : stream(frame("plan", plan({ passed: false, next_actions: [action] })), DONE),
    });
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: label }));
    expect(await screen.findByText("Relaxed.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")[1].body).toEqual({ text, locale: "en" });
  });

  it("shows the contradiction hint for a composition question", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(
          frame("interrupt", { kind: "question", fields: ["composition"], problem: "split_exceeds_total" }),
          DONE,
        ),
    });
    await send("dinner");
    expect(await screen.findByText(/add up to more than the total/)).toBeInTheDocument();
  });

  it("shows the conflict hint for counts that do not fit the total", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(
          frame("interrupt", { kind: "question", fields: ["composition"], problem: "split_conflicts_total" }),
          DONE,
        ),
    });
    await send("dinner");
    expect(await screen.findByText(/do not fit the total number of dishes/)).toBeInTheDocument();
  });

  it("shows the too-many hint for a composition question", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "question", fields: ["composition"], problem: "too_many" }), DONE),
    });
    await send("dinner");
    expect(await screen.findByText(/at most 12 dishes in total/)).toBeInTheDocument();
  });

  it("restores a plan saved before the new fields existed without crashing", async () => {
    window.localStorage.setItem(THREAD_KEY, "t8");
    const old = plan({
      warnings: [{ code: "S1", recipe_id: 1, detail: { name: "oil", needed: 2, have: 1, unit: "ml" } }],
    }) as unknown as Record<string, unknown>;
    for (const key of ["reasons", "defaults_used", "next_actions", "servings_notes", "unmet"]) delete old[key];
    old.spec = { mode: "fridge_only", dishes: 1, soups: 0, time_limit_min: null };
    setup({
      "GET /api/chat/threads/t8": () => ({ messages: [], current_plan: old, pending: null }),
    });
    expect(await screen.findByText(/check the actual ingredients for allergens/)).toBeInTheDocument();
    expect(screen.getByText("oil: 葱花蛋 2, 2 ml needed, 1 ml in stock.")).toBeInTheDocument();
  });

  it("falls back to the standard hint for an unknown problem", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "question", fields: ["composition"], problem: "brand_new" }), DONE),
    });
    await send("dinner");
    expect(await screen.findByText(/how many dishes you want/)).toBeInTheDocument();
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

  const styleSwitch = () => screen.findByRole("switch", { name: "Ask me first when details are missing" });

  it("shows the saved request style on the switch and saves a toggle at once", async () => {
    setup({
      "GET /api/preferences/request-style": () => ({ value: "ask_first" }),
      "PUT /api/preferences/request-style": (body) => body,
    });
    const toggle = await styleSwitch();
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    await userEvent.click(toggle);
    await waitFor(() => expect(callsTo(calls, "PUT", "/api/preferences/request-style")).toHaveLength(1));
    expect(callsTo(calls, "PUT", "/api/preferences/request-style")[0].body).toEqual({
      value: "plan_then_adjust",
    });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
  });

  it("moves the switch when the request-style prompt is answered", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () => stream(DONE),
    });
    const toggle = await styleSwitch();
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Ask me first" }));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
  });

  it("shows the switch off when the prompt is skipped", async () => {
    setup({
      "GET /api/preferences/request-style": () => ({ value: "ask_first" }),
      "POST /api/chat/threads/t1/messages": () =>
        stream(frame("interrupt", { kind: "request_style" }), DONE),
      "POST /api/chat/threads/t1/resume": () => stream(DONE),
    });
    const toggle = await styleSwitch();
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    await send("dinner");
    await userEvent.click(await screen.findByRole("button", { name: "Skip" }));
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")).toHaveLength(1));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
  });

  it("removes the pushed question and restores the prompt when a request-style choice fails", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        items: [{ type: "user", text: "dinner" }],
        messages: [],
        current_plan: null,
        pending: { kind: "request_style" },
      }),
      "POST /api/chat/threads/t9/resume": () => reply(500, { code: "internal" }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Ask me first" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // One copy of the question: the restored prompt, not the pushed item as well.
    expect(screen.getAllByText(/When you leave details out/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Skip" })).toBeEnabled();
  });

  it("shows the welcome state for an empty conversation and an example fills the input only", async () => {
    setup();
    expect(await screen.findByRole("heading", { name: "MineMyDinner" })).toBeInTheDocument();
    expect(screen.getByText(/Dig tonight's dinner out/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dinner for three, and shopping is fine" }));
    expect(screen.getByRole("textbox", { name: "Your message" })).toHaveValue("Dinner for three, and shopping is fine");
    expect(callsTo(calls, "POST", "/api/chat/threads")).toHaveLength(0);
    expect(calls.some((call) => call.path.endsWith("/messages"))).toBe(false);
  });

  it("does not flash the welcome while a stored thread is loading", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({ "GET /api/chat/threads/t9": () => new Promise(() => {}) as never });
    await screen.findByText("Loading the conversation…");
    expect(screen.queryByRole("heading", { name: "MineMyDinner" })).toBeNull();
  });

  it("disables New conversation while the stored thread is loading", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({ "GET /api/chat/threads/t9": () => new Promise(() => {}) as never });
    await screen.findByText("Loading the conversation…");
    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
  });

  it("shows an answered question between the user messages of a stored thread", async () => {
    window.localStorage.setItem(THREAD_KEY, "t4");
    setup({
      "GET /api/chat/threads/t4": () => ({
        items: [
          { type: "user", text: "dinner" },
          { type: "question", value: { kind: "question", fields: ["mode"] } },
          { type: "user", text: "yes" },
        ],
        messages: [],
        current_plan: null,
        pending: null,
      }),
    });
    await screen.findByText(/whether to cook only from the fridge/);
    const texts = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(texts[0]).toMatch(/dinner/);
    expect(texts[1]).toMatch(/whether to cook only from the fridge/);
    expect(texts[2]).toMatch(/yes/);
  });

  it("reverts the switch when a request-style choice fails", async () => {
    window.localStorage.setItem(THREAD_KEY, "t9");
    setup({
      "GET /api/chat/threads/t9": () => ({
        items: [{ type: "user", text: "dinner" }],
        messages: [],
        current_plan: null,
        pending: { kind: "request_style" },
      }),
      "POST /api/chat/threads/t9/resume": () => reply(500, { code: "internal" }),
    });
    const toggle = await styleSwitch();
    await userEvent.click(await screen.findByRole("button", { name: "Ask me first" }));
    await screen.findByRole("alert");
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("leaves the welcome state after the first message and returns after a new conversation", async () => {
    setup({ "POST /api/chat/threads/t1/messages": () => stream(frame("message", { role: "assistant", text: "Hi." }), DONE) });
    await send("hello");
    expect(await screen.findByText("Hi.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "MineMyDinner" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByRole("heading", { name: "MineMyDinner" })).toBeInTheDocument();
  });

  it("sends with Enter and not with Shift+Enter", async () => {
    setup({ "POST /api/chat/threads/t1/messages": () => stream(DONE) });
    const box = screen.getByRole("textbox", { name: "Your message" });
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(calls.some((call) => call.path.endsWith("/messages"))).toBe(false);
    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/messages")[0].body).toEqual({
      text: "line one\nline two",
      locale: "en",
    });
  });

  it("keeps partial streaming text out of the live log", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const encoder = new TextEncoder();
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              controller = c;
              c.enqueue(encoder.encode(frame("token", { text: "Partial" })));
            },
          }),
        ),
    });
    await send("hi");
    const partial = await screen.findByText("Partial");
    expect(partial.closest("[aria-live=polite]")).toBeNull();
    controller.enqueue(encoder.encode(frame("message", { role: "assistant", text: "Final" }) + DONE));
    controller.close();
    const final = await screen.findByText("Final");
    expect(final.closest("[aria-live=polite]")).not.toBeNull();
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

  const twoPlans = {
    items: [
      { type: "user", text: "dinner" },
      { type: "assistant", id: "a1", text: "First plan.", plan: plan({ next_actions: ["fewer_dishes"] }) },
      { type: "user", text: "more" },
      { type: "assistant", id: "a2", text: "Second plan.", plan: plan({ next_actions: ["switch_to_shopping"] }) },
    ],
    messages: [],
    current_plan: plan(),
    pending: null,
  };

  it("shows each reply with its own card and only the newest is actionable", async () => {
    window.localStorage.setItem(THREAD_KEY, "t5");
    setup({ "GET /api/chat/threads/t5": () => twoPlans });
    expect(await screen.findByText("Second plan.")).toBeInTheDocument();
    expect(screen.getAllByText(/check the actual ingredients for allergens/)).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Fewer dishes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Allow shopping" })).toBeEnabled();
  });

  it("keeps the newest card actionable after a later reply without a plan", async () => {
    window.localStorage.setItem(THREAD_KEY, "t5");
    setup({
      "GET /api/chat/threads/t5": () => twoPlans,
      "POST /api/chat/threads/t5/messages": () =>
        stream(frame("message", { role: "assistant", text: "Just chatting." }), DONE),
    });
    await screen.findByText("Second plan.");
    await send("thanks");
    expect(await screen.findByText("Just chatting.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow shopping" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Fewer dishes" })).toBeDisabled();
  });

  it("disables the newest card while a question is pending", async () => {
    window.localStorage.setItem(THREAD_KEY, "t5");
    setup({ "GET /api/chat/threads/t5": () => ({ ...twoPlans, pending: { kind: "question", fields: ["mode"] } }) });
    await screen.findByText(/whether to cook only from the fridge/);
    expect(screen.getByRole("button", { name: "Allow shopping" })).toBeDisabled();
  });

  it("disables the newest card while a run is busy", async () => {
    window.localStorage.setItem(THREAD_KEY, "t5");
    setup({
      "GET /api/chat/threads/t5": () => twoPlans,
      "POST /api/chat/threads/t5/messages": () => new Promise(() => {}) as never,
    });
    await screen.findByText("Second plan.");
    await send("again");
    await waitFor(() => expect(screen.getByRole("button", { name: "Allow shopping" })).toBeDisabled());
  });

  it("renders a streamed plan with no message as a card-only reply", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () => stream(frame("plan", plan({ next_actions: ["fewer_dishes"] })), DONE),
    });
    await send("dinner");
    expect(await screen.findByRole("button", { name: "Fewer dishes" })).toBeEnabled();
    expect(screen.getAllByRole("listitem").filter((li) => li.className.includes("chat-assistant"))).toHaveLength(1);
  });

  it("shows the plan of an old thread as a card at the end, actionable", async () => {
    window.localStorage.setItem(THREAD_KEY, "t6");
    setup({
      "GET /api/chat/threads/t6": () => ({
        messages: [
          { role: "user", text: "dinner" },
          { role: "assistant", text: "Old reply." },
        ],
        current_plan: plan({ next_actions: ["fewer_dishes"] }),
        pending: null,
      }),
    });
    expect(await screen.findByRole("button", { name: "Fewer dishes" })).toBeEnabled();
    const items = screen.getAllByRole("listitem").filter((li) => li.className.includes("chat-message"));
    expect(items[items.length - 1]).toContainElement(screen.getByRole("button", { name: "Fewer dishes" }));
  });

  it("shows a pending question as an assistant item and keeps it after the answer", async () => {
    window.localStorage.setItem(THREAD_KEY, "t7");
    setup({
      "GET /api/chat/threads/t7": () => ({
        items: [{ type: "user", text: "dinner" }],
        messages: [],
        current_plan: null,
        pending: { kind: "question", fields: ["mode"] },
      }),
      "POST /api/chat/threads/t7/resume": () => stream(frame("message", { role: "assistant", text: "Thanks." }), DONE),
    });
    const hint = await screen.findByText(/whether to cook only from the fridge/);
    expect(hint.closest("li")).toHaveClass("chat-assistant");
    await send("yes");
    expect(await screen.findByText("Thanks.")).toBeInTheDocument();
    const texts = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(texts[1]).toMatch(/whether to cook only from the fridge/);
    expect(texts[2]).toMatch(/yes/);
  });

  it("shows the diner card for a diners interrupt and resumes with the answer object", async () => {
    const diners = {
      kind: "diners",
      headcount: 2,
      rows: [
        { diner_id: 1, name: null, is_self: true, selected: true, rules: [], proposed: [], diet_words: [] },
        { diner_id: 2, name: "Xiao Wang", is_self: false, selected: true, rules: [], proposed: [], diet_words: [] },
      ],
      guests: [],
      others: 0,
      nudge: false,
    };
    setup({
      "POST /api/chat/threads/t1/messages": () => stream(frame("interrupt", diners), DONE),
      "POST /api/chat/threads/t1/resume": () =>
        stream(frame("message", { role: "assistant", text: "Planning for two." }), DONE),
    });
    await send("dinner with Xiao Wang");
    const confirm = await screen.findByRole("button", { name: "Confirm and start planning" });
    expect(screen.getByRole("checkbox", { name: "Xiao Wang" })).toBeChecked();
    await userEvent.click(confirm);
    expect(await screen.findByText("Planning for two.")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/chat/threads/t1/resume")[0].body).toEqual({
      value: { selected: [1, 2], stated: {}, guests: [], others: 0, others_answer: null },
      locale: "en",
    });
    expect(screen.queryByRole("button", { name: "Confirm and start planning" })).toBeNull();
    expect(screen.getByText("Diners confirmed: Xiao Wang.")).toBeInTheDocument();
  });

  it("keeps the diner card's edits when confirming fails", async () => {
    setup({
      "POST /api/chat/threads/t1/messages": () =>
        stream(
          frame("interrupt", {
            kind: "diners",
            headcount: 2,
            rows: [{ diner_id: 1, name: null, is_self: true, selected: true, rules: [], proposed: [], diet_words: [] }],
            guests: [],
            others: 1,
            nudge: false,
          }),
          DONE,
        ),
      "POST /api/chat/threads/t1/resume": () => reply(500, { code: "unknown" }),
    });
    await send("dinner for two");
    await userEvent.click(await screen.findByRole("button", { name: "Add a person" }));
    await userEvent.type(screen.getByLabelText("Name"), "Mia");
    await userEvent.click(screen.getByRole("button", { name: "Confirm and start planning" }));
    await screen.findByRole("alert");
    expect(await screen.findByLabelText("Name")).toHaveValue("Mia");
    expect(screen.queryByText(/Diners confirmed/)).toBeNull();
  });

  it("sends a typed message as a new message while the diner card is pending", async () => {
    window.localStorage.setItem(THREAD_KEY, "t5");
    setup({
      "GET /api/chat/threads/t5": () => ({
        items: [{ type: "user", text: "dinner" }],
        pending: { kind: "diners", headcount: 1, rows: [], guests: [], others: 1, nudge: false },
      }),
      "POST /api/chat/threads/t5/messages": () => stream(DONE),
    });
    await screen.findByRole("button", { name: "Confirm and start planning" });
    await send("never mind");
    await waitFor(() => expect(callsTo(calls, "POST", "/api/chat/threads/t5/messages")).toHaveLength(1));
    expect(callsTo(calls, "POST", "/api/chat/threads/t5/resume")).toHaveLength(0);
  });

  it("shows the summary of an answered diner card in a stored thread", async () => {
    window.localStorage.setItem(THREAD_KEY, "t6");
    setup({
      "GET /api/chat/threads/t6": () => ({
        items: [
          { type: "user", text: "dinner" },
          { type: "question", value: { kind: "diners", summary: { names: ["Xiao Wang", "Alex"], others: 2 } } },
          { type: "question", value: { kind: "diners", summary: null } },
        ],
        pending: null,
      }),
    });
    expect(
      await screen.findByText("Diners confirmed: Xiao Wang, Alex, and 2 more people."),
    ).toBeInTheDocument();
    expect(screen.getByText("Who is eating?")).toBeInTheDocument();
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
