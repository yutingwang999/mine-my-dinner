import { render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { vi } from "vitest";

import en from "../../messages/en.json";
import type { RecipeDraft } from "@/lib/api";
import { DraftsProvider, useDrafts } from "@/lib/drafts";

type Handler = (body: unknown, init: RequestInit, url: string) => unknown;
export type Call = { method: string; path: string; url: string; body: unknown };

/** JSON response helper for route handlers. */
export function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Replace fetch with handlers keyed by "METHOD /path". A handler returns a body for
 * a 200 response, or a Response. Every call is recorded.
 */
export function mockFetch(routes: Record<string, Handler>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const path = input.split("?")[0];
      const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      calls.push({ method, path, url: input, body });
      const handler = routes[`${method} ${path}`];
      if (!handler) {
        return reply(404, { code: "unknown" });
      }
      const result = await handler(body, init, input);
      return result instanceof Response ? result : reply(200, result);
    }),
  );
  return calls;
}

export function callsTo(calls: Call[], method: string, path: string): Call[] {
  return calls.filter((call) => call.method === method && call.path === path);
}

function QueueProbe() {
  const { drafts } = useDrafts();
  return <output data-testid="queue">{drafts.map((draft) => draft.title).join("|")}</output>;
}

export function renderWithProviders(ui: ReactNode, drafts: RecipeDraft[] = []) {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <DraftsProvider initialDrafts={drafts}>
        {ui}
        <QueueProbe />
      </DraftsProvider>
    </NextIntlClientProvider>,
  );
}

export function draft(fields: Partial<RecipeDraft> = {}): RecipeDraft {
  return {
    lang: "zh",
    title: "葱花蛋",
    dish_type: "vegetable dish",
    time_min: 10,
    time_source: "stated",
    servings: 2,
    tags: [],
    steps_summary: "鸡蛋炒熟后撒葱花。",
    ingredients: [
      {
        raw: "少量葱花",
        name: "葱花",
        amount: null,
        unit: null,
        amount_type: "vague",
        role: "secondary",
        optional: false,
        ingredient_id: 1,
        // 葱花 is an alias of the entry 葱, so the names differ on purpose.
        linked_name_zh: "葱",
        linked_name_en: "scallion",
      },
      {
        raw: "秘制酱",
        name: "秘制酱",
        amount: null,
        unit: null,
        amount_type: "missing",
        role: "seasoning",
        optional: false,
        ingredient_id: null,
      },
    ],
    cookware: [
      { name: "炒锅", cookware_id: 3 },
      { name: "神奇锅", cookware_id: null },
    ],
    source_type: "text",
    source_url: null,
    provenance: {
      input_text: "葱花蛋",
      model_output: { title: "葱花蛋" },
      model_name: "gemini",
      prompt_version: "extract_v1",
    },
    ...fields,
  };
}
